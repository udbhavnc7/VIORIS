"""
Task Manager (Phase 2) — persistence + run loop.

Wraps the state-machine engine with SQLite persistence and an audit chain so
the Phase 2 exit test (pause from a CLI call, resume later, failing step
retries without duplicated side effects) works across process restarts.

The task and its steps are stored as JSON documents; audit events are appended
to a hash-chained table exactly like Phase 1's local store. The manager is
single-writer (only the orchestrator/CLI touches a given task).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path

from packages.shared.audit import GENESIS_HASH, event_payload
from packages.shared.explainability import (
    ExplainabilityEngine,
    ReasoningStore,
    StepReasoning,
)
from packages.shared.explainability_handler import ExplainabilityHandler
from packages.shared.schemas import Task, TaskStatus, TaskStep

from .approvals import ApprovalStore, action_hash
from .engine import StepOutcome, TaskEngine
from .events import EventHub
from .staged_execution import StagedAction, StagedActionStore

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    request    TEXT NOT NULL,
    status     TEXT NOT NULL,
    body       TEXT NOT NULL,          -- full JSON Task document (incl. steps)
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   TEXT NOT NULL UNIQUE,
    task_id    TEXT,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}',
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class TaskManager:
    def __init__(self, db_path: str | Path, engine: TaskEngine | None = None, events: EventHub | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False: FastAPI serves requests from a threadpool;
        # the manager serializes every access through _lock, so one connection
        # may safely cross threads.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.approvals = ApprovalStore(self.db_path)
        self.events = events or EventHub()
        self.engine = engine or TaskEngine(approval_store=self.approvals)
        # Explainability lives beside the audit chain in the same local DB so
        # "why did you do that?" is answered from the same durable record.
        self.reasoning = ReasoningStore(self.db_path)
        self.explainer = ExplainabilityEngine(self.reasoning)
        self.why_handler = ExplainabilityHandler(self.explainer)
        # Critical actions are staged here after approval so the user gets a
        # cancel window before an irreversible step fires. In-memory by design:
        # a restart leaves the step armed but never silently executes it.
        self.stages = StagedActionStore()
        self._reconcile_inflight()

    def _reconcile_inflight(self) -> None:
        """A step left RUNNING by a crash has an UNKNOWN external outcome.

        We mark it failed instead of re-running it: retrying could duplicate a
        side effect that already happened. The user can retry explicitly,
        which re-arms it under the same idempotency key.
        """
        for task in self.list_tasks(limit=100000):
            changed = False
            for step in task.steps:
                if step.status == TaskStatus.RUNNING:
                    step.status = TaskStatus.FAILED
                    step.error = "process stopped during execution — outcome unknown; not retried automatically"
                    changed = True
            if changed and task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.FAILED
                self._persist(task)
                self.append_audit(
                    "system",
                    "failed",
                    {"reason": "crash recovery: in-flight step outcome unknown"},
                    task.task_id,
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
        self.reasoning.close()

    # ── write path ────────────────────────────────────────────────────────
    def create_task(self, request: str, steps: list[TaskStep]) -> Task:
        task = self.engine.create_task(request, steps)
        self._persist(task)
        for step in task.steps:
            self._record_reasoning(task, step, request)
        self.append_audit(
            "system", "planned_step", {"request": request, "steps": len(steps)}, task.task_id
        )
        return task

    def _record_reasoning(self, task: Task, step: TaskStep, request: str) -> None:
        """Persist the planner's reasoning for a step, with a safe fallback.

        Older/API-created plans may not carry a reasoning string; the fallback
        keeps the record honest about what actually drove the decision (the
        static registry tier) instead of inventing a story.
        """
        chosen = step.reasoning or (
            f"The plan used {step.tool} for this request; its "
            f"{step.risk_level.value}-tier risk came from the static permission registry."
        )
        rationale = (
            f"{step.tool} is {step.risk_level.value}-tier in the frozen registry, "
            "so the gate matches the action's real blast radius."
        )
        self.reasoning.store(
            StepReasoning(
                task_id=task.task_id,
                step_id=step.step_id,
                tool=step.tool,
                chosen_reason=chosen,
                alternatives=step.alternatives or [],
                risk_rationale=rationale,
                user_request=request,
            )
        )

    def start(self, task_id: str) -> Task:
        task = self.get(task_id)
        self.engine.start(task)
        self._persist(task)
        return task

    def pause(self, task_id: str) -> Task:
        task = self.get(task_id)
        self.engine.pause(task)
        self._persist(task)
        self.append_audit(
            "system", "state_transition", {"from": "running", "to": "paused"}, task.task_id
        )
        return task

    def resume(self, task_id: str) -> Task:
        task = self.get(task_id)
        self.engine.resume(task)
        self._persist(task)
        self.append_audit(
            "system", "state_transition", {"from": "paused", "to": "running"}, task.task_id
        )
        return task

    def cancel(self, task_id: str) -> Task:
        task = self.get(task_id)
        self.engine.cancel(task)
        self._persist(task)
        self.append_audit("system", "cancelled", {}, task.task_id)
        return task

    def stop_all(self) -> list[str]:
        """Emergency stop (Phase 5 'Stop Everything'): halt every
        non-terminal task. Any step still WAITING_APPROVAL is purged so a
        late Approve can never fire it — stop is a hard stop."""
        affected: list[str] = []
        tasks = self.list_tasks(limit=100000)
        for task in tasks:
            if task.status.value in ("cancelled", "completed", "failed", "stopped"):
                continue
            try:
                self.engine.stop(task)
            except Exception:  # noqa: BLE001 — a stuck task must not block the rest
                task.status = TaskStatus.STOPPED
            self._persist(task)
            self.append_audit("system", "stopped", {"emergency": True}, task.task_id)
            affected.append(task.task_id)
            for approval in self.approvals.list_pending(task.task_id):
                self.approvals.reject(approval)
        if affected:
            self.events.publish({"type": "stopped_all", "task_ids": affected})
        return affected

    def kill_phrase(self, transcript: str) -> list[str]:
        """LLM-independent emergency stop triggered by a kill phrase.

        The kill phrase is matched before planning, so this path must never
        depend on the planner or an approval. It reuses ``stop_all()`` (hard
        stop, pending approvals purged) and always writes an audit record,
        even when no task was live at the moment the phrase was heard.
        """
        affected = self.stop_all()
        self.append_audit(
            "system",
            "stopped",
            {
                "emergency": True,
                "trigger": "kill_phrase",
                "transcript": transcript,
                "affected": affected,
            },
        )
        self.events.publish(
            {"type": "kill_phrase", "task_ids": affected, "transcript": transcript}
        )
        return affected

    def retry_step(self, task_id: str, step_id: str) -> Task:
        task = self.get(task_id)
        events = self.engine.retry_step(task, step_id)
        for ev in events:
            self.append_audit(ev["actor"], ev["action"], ev["detail"], task.task_id)
        self._persist(task)
        return task

    def run_next_step(self, task_id: str, execute: callable) -> tuple[Task, StepOutcome]:
        """Run one pending step; if it was the last, finalize.
        If the engine pauses for approval, create the ApprovalRequest."""
        task = self.get(task_id)

        # A Critical step inside its cancel window must not be fired by any
        # other caller (UI "Run", resume, a retry). Only the stage timer may
        # advance it, and it marks the stage EXECUTING before dispatching.
        if self.stages.has_pending_for_task(task_id):
            return task, StepOutcome(
                None, ok=False, error="awaiting staged execution window"
            )

        # `system.stop` is the user's kill switch. Route it to the manager so
        # it halts EVERY task, not merely the one that requested it.
        next_step = next((s for s in task.steps if s.status == TaskStatus.CREATED), None)
        if (
            task.status in (TaskStatus.CREATED, TaskStatus.RUNNING)
            and next_step is not None
            and next_step.tool == "system.stop"
        ):
            stopped = self.stop_all()
            stopped_task = self.get(task_id)
            return stopped_task, StepOutcome(
                next_step, ok=True, result={"ok": True, "stopped": stopped}
            )

        def _wrapped(step, run_task):
            # Persist the RUNNING marker before the external call so a crash
            # leaves evidence and is reconciled instead of silently re-run.
            self._persist(run_task)
            return execute(step, run_task)

        outcome, events = self.engine.run_next_step(task, _wrapped)
        for ev in events:
            self.append_audit(ev["actor"], ev["action"], ev["detail"], task.task_id)
        if outcome.step is not None and outcome.step.status == TaskStatus.WAITING_APPROVAL:
            args = (outcome.step.result or {}).get("args") or {}
            approval = self.approvals.create(
                task_id,
                outcome.step.step_id,
                outcome.step.idempotency_key,
                outcome.step.tool,
                args,
            )
            self.events.publish(
                {
                    "type": "pending_approval",
                    "approval_id": approval.approval_id,
                    "task_id": task_id,
                    "step_id": outcome.step.step_id,
                    "tool": outcome.step.tool,
                    "idempotency_key": outcome.step.idempotency_key,
                    "diff_card": approval.diff_card,
                }
            )
        if all(s.status != TaskStatus.CREATED for s in task.steps):
            for ev in self.engine.finalize(task):
                self.append_audit(ev["actor"], ev["action"], ev["detail"], task.task_id)
        self._persist(task)
        return task, outcome

    # ── approval orchestration (Prompt 2.3) ───────────────────────────────
    def pending_approvals(self, task_id: str | None = None) -> list[dict]:
        return [a.__dict__ for a in self.approvals.list_pending(task_id)]

    def approve(self, task_id: str, approval_id: str) -> Task:
        """Explicit approve for ONE step, tied to its idempotency_key. Resume."""
        task = self.get(task_id)
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"no approval {approval_id}")
        # NEVER run: the approval must belong to this task and to the step that
        # is actually sitting in it under the approved idempotency_key. A stale
        # or cross-task approval can't fire a new run.
        if approval.task_id != task_id:
            raise ValueError("approval does not belong to this task")
        step = next((s for s in task.steps if s.step_id == approval.step_id), None)
        if step is None:
            raise ValueError("approval step is not pending on this task")
        if approval.idempotency_key != step.idempotency_key:
            raise ValueError("approval does not match this step's current idempotency key")
        # Consent is bound to the exact action: if the recipient/content/tool
        # changed after the card was shown, the approval is stale and must not fire.
        current_args = (step.result or {}).get("args") or {}
        if approval.tool != step.tool:
            raise ValueError("approval tool does not match the pending step")
        if approval.action_hash and approval.action_hash != action_hash(step.tool, current_args):
            raise ValueError("approval was granted for different action contents")
        self.approvals.approve(approval)
        self.engine.arm_step(task, step.step_id)  # WAITING_APPROVAL -> CREATED
        self._persist(task)
        self.events.publish(
            {
                "type": "approved",
                "approval_id": approval.approval_id,
                "task_id": task.task_id,
                "step_id": step.step_id,
                "idempotency_key": approval.idempotency_key,
                "tool": step.tool,
            }
        )
        self.append_audit("system", "approved", {"approval": approval_id, "step": step.step_id}, task.task_id)
        return task

    def reject(self, task_id: str, approval_id: str) -> Task:
        task = self.get(task_id)
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"no approval {approval_id}")
        self.approvals.reject(approval)
        step = next((s for s in task.steps if s.step_id == approval.step_id), None)
        if step is not None:
            step.status = TaskStatus.CANCELLED  # abandoned — never fires
            step.error = "rejected by user"
        if all(s.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED) for s in task.steps):
            task.status = TaskStatus.CANCELLED
        else:
            task.status = TaskStatus.PAUSED
        self._persist(task)
        self.events.publish(
            {
                "type": "rejected",
                "approval_id": approval.approval_id,
                "task_id": task.task_id,
                "step_id": approval.step_id,
                "idempotency_key": approval.idempotency_key,
                "tool": step.tool if step is not None else None,
            }
        )
        self.append_audit("user", "rejected", {"approval": approval_id, "step": approval.step_id}, task.task_id)
        return task

    # ── staged Critical execution (reversible-by-default) ─────────────────
    def stage_critical(
        self,
        task_id: str,
        step_id: str,
        tool: str,
        description: str,
        diff_card: dict,
        execute_fn,
        undo_fn=None,
        execute_after_seconds: float = 30.0,
        undo_window_seconds: float = 300.0,
    ) -> StagedAction:
        """Stage an already-approved Critical step with a cancel/undo window.

        The execute callback still goes through the normal dispatch path, so
        the permission registry and approval ledger are re-checked when the
        window expires. Approval is never inferred from staging.
        """
        stage_id = f"stage_{uuid.uuid4().hex[:8]}"

        def run_and_audit():
            try:
                result = execute_fn()
            except Exception:
                self.append_audit(
                    "system",
                    "stage_failed",
                    {"stage_id": stage_id, "tool": tool},
                    task_id,
                )
                raise
            self.append_audit(
                "system",
                "stage_executed",
                {"stage_id": stage_id, "tool": tool},
                task_id,
            )
            return result

        action = self.stages.stage(
            tool=tool,
            task_id=task_id,
            step_id=step_id,
            description=description,
            diff_card=diff_card,
            execute_fn=run_and_audit,
            undo_fn=undo_fn,
            execute_after_seconds=execute_after_seconds,
            undo_window_seconds=undo_window_seconds,
            stage_id=stage_id,
        )
        self.append_audit(
            "system",
            "staged",
            {
                "stage_id": action.stage_id,
                "tool": tool,
                "step": step_id,
                "execute_after_seconds": execute_after_seconds,
                "undo_window_seconds": undo_window_seconds,
            },
            task_id,
        )
        self.events.publish(
            {
                "type": "staged",
                "stage_id": action.stage_id,
                "task_id": task_id,
                "step_id": step_id,
                "tool": tool,
                "execute_after_seconds": execute_after_seconds,
                "undo_window_seconds": undo_window_seconds,
            }
        )
        return action

    def list_stages(self) -> list[dict]:
        actions = self.stages.list_pending() + self.stages.list_undoable()
        return [self._stage_dict(a) for a in actions]

    def cancel_staged(self, stage_id: str) -> dict:
        action = self.stages.get(stage_id)
        if action is None:
            raise KeyError(f"no stage {stage_id}")
        if not self.stages.cancel(stage_id):
            raise ValueError("stage is already executing, executed, or cancelled")
        # Cancelling the stage must also cancel the armed step/task; otherwise
        # a later /run could still fire what the user just stopped.
        try:
            self.cancel(action.task_id)
        except (KeyError, ValueError):
            logger.warning("staged cancel: task %s already terminal", action.task_id)
        self.append_audit(
            "user",
            "stage_cancelled",
            {"stage_id": stage_id, "tool": action.tool, "step": action.step_id},
            action.task_id,
        )
        self.events.publish(
            {
                "type": "stage_cancelled",
                "stage_id": stage_id,
                "task_id": action.task_id,
                "step_id": action.step_id,
                "tool": action.tool,
            }
        )
        return self._stage_dict(action)

    def undo_staged(self, stage_id: str) -> dict:
        action = self.stages.get(stage_id)
        if action is None:
            raise KeyError(f"no stage {stage_id}")
        if not self.stages.undo(stage_id):
            raise ValueError("stage is not undoable (not executed, expired, or no reversal handler)")
        self.append_audit(
            "user",
            "undone",
            {"stage_id": stage_id, "tool": action.tool, "step": action.step_id},
            action.task_id,
        )
        self.events.publish(
            {
                "type": "undone",
                "stage_id": stage_id,
                "task_id": action.task_id,
                "step_id": action.step_id,
                "tool": action.tool,
            }
        )
        return self._stage_dict(action)

    @staticmethod
    def _stage_dict(action: StagedAction) -> dict:
        return {
            "stage_id": action.stage_id,
            "tool": action.tool,
            "task_id": action.task_id,
            "step_id": action.step_id,
            "description": action.description,
            "status": action.status.value,
            "diff_card": action.diff_card,
            "created_at": action.created_at,
            "execute_after_seconds": action.execute_after_seconds,
            "undo_window_seconds": action.undo_window_seconds,
            "undoable": action.status.value == "executed" and action.undo_fn is not None,
        }

    # ── read / helpers ────────────────────────────────────────────────────
    def get(self, task_id: str) -> Task:
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"task {task_id} not found")
        data = json.loads(row[0])
        return Task(**data)

    def list_tasks(self, limit: int = 50) -> list[Task]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT body FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Task(**json.loads(r[0])) for r in rows]

    def append_audit(
        self, actor: str, action: str, detail: dict, task_id: str | None = None
    ) -> str:
        detail = detail or {}
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        with self._lock:
            row = self._conn.execute(
                "SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev = row[0] if row else GENESIS_HASH
            payload = prev + event_payload(event_id, actor, action, detail)
            hash_val = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            self._conn.execute(
                "INSERT INTO audit_events (event_id, task_id, actor, action, detail, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    task_id,
                    actor,
                    action,
                    json.dumps(detail, sort_keys=True),
                    prev,
                    hash_val,
                ),
            )
            self._conn.commit()
        return event_id

    def audit_events_copy(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id, actor, action, detail, prev_hash, hash, created_at "
                "FROM audit_events ORDER BY seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "task_id": r[0],
                "actor": r[1],
                "action": r[2],
                "detail": json.loads(r[3]),
                "prev_hash": r[4],
                "hash": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    def verify_chain(self) -> tuple[bool, list[str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, actor, action, detail, prev_hash, hash "
                "FROM audit_events ORDER BY seq ASC"
            ).fetchall()
        prev = GENESIS_HASH
        problems = []
        for r in rows:
            event_id, actor, action, detail, prev_hash, hash = r
            if prev_hash != prev:
                problems.append(f"{event_id}: prev_hash mismatch")
            payload = prev + event_payload(event_id, actor, action, json.loads(detail or "{}"))
            if hash != hashlib.sha256(payload.encode("utf-8")).hexdigest():
                problems.append(f"{event_id}: hash mismatch")
            prev = hash
        return (not problems, problems)

    # ── explainability (Phase 8.4 / 10.2) ─────────────────────────────────
    def _reasoning_records(self, task: Task) -> list[dict]:
        """Return stored reasoning for a task, falling back to the step itself.

        Tasks created before explainability was wired still have their plan on
        disk. Rather than answer "no reasoning found", we surface the step's
        own reasoning field when present and an honest registry-based fallback
        otherwise. The record is never fabricated as if the planner had said it.
        """
        stored = {r.step_id: r for r in self.reasoning.get_by_task(task.task_id)}
        records: list[dict] = []
        for step in task.steps:
            reason = stored.get(step.step_id)
            if reason is not None:
                records.append(
                    {
                        "step_id": reason.step_id,
                        "tool": reason.tool,
                        "chosen_reason": reason.chosen_reason,
                        "alternatives": reason.alternatives,
                        "risk_rationale": reason.risk_rationale,
                        "user_request": reason.user_request,
                        "created_at": reason.created_at,
                        "source": "recorded",
                    }
                )
                continue
            records.append(
                {
                    "step_id": step.step_id,
                    "tool": step.tool,
                    "chosen_reason": step.reasoning
                    or (
                        f"This plan step used {step.tool}; its "
                        f"{step.risk_level.value}-tier risk came from the static permission registry."
                    ),
                    "alternatives": step.alternatives,
                    "risk_rationale": (
                        f"{step.tool} is {step.risk_level.value}-tier in the frozen registry."
                    ),
                    "user_request": task.request,
                    "created_at": step.created_at.isoformat(),
                    "source": "derived",
                }
            )
        return records

    def explain_task(self, task_id: str) -> dict:
        task = self.get(task_id)
        records = self._reasoning_records(task)
        explanation = self.explainer.explain_task(task_id)
        if not self.reasoning.get_by_task(task_id) and records:
            # The engine has no stored rows; render the derived records so the
            # answer still names every step instead of claiming ignorance.
            lines = ["Here's why I did what I did for that task:"]
            for i, record in enumerate(records, 1):
                lines.append(f"\nStep {i} ({record['tool']}): {record['chosen_reason']}")
            explanation = "\n".join(lines)
        return {
            "task_id": task_id,
            "request": task.request,
            "status": task.status.value,
            "explanation": explanation,
            "steps": records,
            "found": bool(records),
        }

    def explain_step(self, task_id: str, step_id: str) -> dict:
        task = self.get(task_id)
        record = next((r for r in self._reasoning_records(task) if r["step_id"] == step_id), None)
        if record is None:
            raise KeyError(f"step {step_id} not found on task {task_id}")
        explanation = self.explainer.explain_step(task_id, step_id)
        if record["source"] == "derived":
            parts = [f"I used {record['tool']} because {record['chosen_reason']}."]
            if record["risk_rationale"]:
                parts.append(f"Risk assessment: {record['risk_rationale']}")
            explanation = "\n".join(parts)
        return {
            "task_id": task_id,
            "step_id": step_id,
            **record,
            "explanation": explanation,
        }

    def explain_query(self, text: str) -> dict:
        """Answer a natural-language "why did you do that?" question."""
        response = self.why_handler.handle(text)
        return {
            "query": text,
            "explanation": response.explanation,
            "found": response.found,
            "query_type": response.query_type,
        }

    def recent_reasoning(self, limit: int = 20) -> list[dict]:
        return [
            {
                "reasoning_id": r.reasoning_id,
                "task_id": r.task_id,
                "step_id": r.step_id,
                "tool": r.tool,
                "chosen_reason": r.chosen_reason,
                "alternatives": r.alternatives,
                "risk_rationale": r.risk_rationale,
                "user_request": r.user_request,
                "created_at": r.created_at,
            }
            for r in self.reasoning.recent(limit=limit)
        ]

    def _persist(self, task: Task) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, request, status, body) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET request=excluded.request, status=excluded.status, body=excluded.body",
                (
                    task.task_id,
                    task.request,
                    task.status.value,
                    json.dumps(task.model_dump(), default=str),
                ),
            )
            self._conn.commit()
