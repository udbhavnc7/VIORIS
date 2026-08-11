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
from packages.shared.schemas import Task, TaskStatus, TaskStep

from .approvals import ApprovalStore
from .engine import StepOutcome, TaskEngine
from .events import EventHub

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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── write path ────────────────────────────────────────────────────────
    def create_task(self, request: str, steps: list[TaskStep]) -> Task:
        task = self.engine.create_task(request, steps)
        self._persist(task)
        self.append_audit(
            "system", "planned_step", {"request": request, "steps": len(steps)}, task.task_id
        )
        return task

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
        tasks = self.list_tasks(limit=500)
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

    def retry_step(self, task_id: str, step_id: str) -> Task:
        task = self.get(task_id)
        self.engine.retry_step(task, step_id)
        self._persist(task)
        self.append_audit("system", "planned_step", {"retry_of": step_id}, task.task_id)
        return task

    def run_next_step(self, task_id: str, execute: callable) -> tuple[Task, StepOutcome]:
        """Run one pending step; if it was the last, finalize.
        If the engine pauses for approval, create the ApprovalRequest."""
        task = self.get(task_id)
        outcome, events = self.engine.run_next_step(task, execute)
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
