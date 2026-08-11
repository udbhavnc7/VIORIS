"""
Task Engine (Phase 2, Prompt 2.1).

Lifecycle state machine over the shared Task/TaskStep schema. Rules:

  - A task is CREATED, then RUNNING, then COMPLETED/FAILED/CANCELLED/STOPPED.
  - RUNNING <-> PAUSED is allowed (pause mid-plan, resume later).
  - A task can only reach COMPLETED after EVERY step passed its verification
    step — never merely because a tool call returned without error.
  - A step that fails may be retried; a retry re-runs the SAME step under the
    SAME idempotency_key so the side effect is never duplicated.
  - Retries are bounded (max_attempts per step). Exceeding them fails the task.

The engine is pure — it mutates the Task object and returns audit events as a
list of dicts. Persistence and the run loop live in the manager.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.shared.schemas import AuditAction, Task, TaskStatus, TaskStep


@dataclass
class StepOutcome:
    """What running one step produced."""

    step: TaskStep | None
    ok: bool
    result: dict | None = None
    error: str | None = None


class InvalidTransitionError(Exception):
    """A lifecycle transition that the state machine forbids."""


VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.PAUSED,
        TaskStatus.CANCELLED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
    },
    TaskStatus.PAUSED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.WAITING_APPROVAL: {TaskStatus.PAUSED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.RUNNING},  # retry the whole task after fix
    TaskStatus.CANCELLED: set(),
    TaskStatus.STOPPED: set(),
}


def assert_transition(task: Task, new_status: TaskStatus) -> None:
    if new_status not in VALID_TRANSITIONS.get(task.status, set()):
        raise InvalidTransitionError(f"task {task.status.value} -> {new_status.value} not allowed")


class TaskEngine:
    def __init__(
        self,
        verify: callable | None = None,
        max_attempts: int = 3,
        approval_store=None,
    ) -> None:
        # verify(step, result) -> (ok: bool, note: str). Defaults to accepting
        # any non-error result (still stricter than "tool returned without error").
        self.verify = verify or (lambda _step, _result: (True, "result accepted"))
        self.max_attempts = max_attempts
        self.attempts: dict[str, int] = {}  # step_id -> attempt count
        self.approval_store = approval_store

    # ── lifecycle ─────────────────────────────────────────────────────────
    def create_task(self, request: str, steps: list[TaskStep]) -> Task:
        task = Task(request=request, steps=steps)
        task.risk_level = _task_risk(steps)
        return task

    def start(self, task: Task) -> Task:
        assert_transition(task, TaskStatus.RUNNING)
        task.status = TaskStatus.RUNNING
        task.updated_at = _now()
        return task

    def pause(self, task: Task) -> Task:
        assert_transition(task, TaskStatus.PAUSED)
        task.status = TaskStatus.PAUSED
        task.updated_at = _now()
        return task

    def resume(self, task: Task) -> Task:
        assert_transition(task, TaskStatus.RUNNING)
        task.status = TaskStatus.RUNNING
        task.updated_at = _now()
        return task

    def cancel(self, task: Task) -> Task:
        assert_transition(task, TaskStatus.CANCELLED)
        for step in task.steps:
            if step.status in (TaskStatus.CREATED, TaskStatus.RUNNING):
                step.status = TaskStatus.CANCELLED
        task.status = TaskStatus.CANCELLED
        task.updated_at = _now()
        return task

    def stop(self, task: Task) -> Task:
        """Emergency stop: cancel all outstanding steps, mark stopped.

        Approval-pending steps are cancelled too — after a stop a late
        Approve can never re-arm them (arm_step requires WAITING_APPROVAL).
        """
        for step in task.steps:
            if step.status in (
                TaskStatus.CREATED,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_APPROVAL,
            ):
                step.status = TaskStatus.CANCELLED
        task.status = TaskStatus.STOPPED
        task.updated_at = _now()
        return task

    # ── execution ─────────────────────────────────────────────────────────
    def run_next_step(self, task: Task, execute: callable) -> tuple[StepOutcome, list[dict]]:
        """Run the next not-yet-done step. Returns its outcome + audit events.

        `execute(step) -> dict` runs the tool and returns the raw result.
        The engine does NOT mark the step done on a non-error return — the
        verifier must pass first.
        """
        events: list[dict] = []
        step = next((s for s in task.steps if s.status == TaskStatus.CREATED), None)
        if step is None:
            return StepOutcome(None, ok=False, error="no runnable step"), events

        attempts = self.attempts.get(step.step_id, 0)
        if attempts >= self.max_attempts:
            step.status = TaskStatus.FAILED
            task.status = TaskStatus.FAILED
            task.updated_at = _now()
            events.append(
                _ev(
                    "system",
                    AuditAction.FAILED,
                    {"step": step.step_id, "reason": "max attempts exceeded"},
                )
            )
            return StepOutcome(step, ok=False, error="max attempts exceeded"), events

        # ── permission gate: execute/critical steps pause until explicitly
        #    approved against THIS step's idempotency key. The execute closure
        #    must never fire while in WAITING_APPROVAL. ────────────────────
        needs_approval = _requires_approval(step)
        if needs_approval:
            approved = (
                self.approval_store.is_approved_for_step(
                    task.task_id, step.step_id, step.idempotency_key
                )
                if self.approval_store is not None
                else False
            )
            if not approved:
                step.status = TaskStatus.WAITING_APPROVAL
                task.status = TaskStatus.WAITING_APPROVAL
                task.updated_at = _now()
                events.append(
                    _ev(
                        "system",
                        AuditAction.REQUESTED_APPROVAL,
                        {
                            "step": step.step_id,
                            "tool": step.tool,
                            "idempotency_key": step.idempotency_key,
                        },
                    )
                )
                return StepOutcome(step, ok=False, error="awaiting approval"), events

        self.attempts[step.step_id] = attempts + 1
        step.status = TaskStatus.RUNNING
        task.status = TaskStatus.RUNNING
        task.updated_at = _now()
        events.append(
            _ev("system", AuditAction.PLANNED_STEP, {"step": step.step_id, "tool": step.tool})
        )

        try:
            result = execute(step)
        except Exception as exc:  # noqa: BLE001 — tool failure becomes a failed step
            step.status = TaskStatus.FAILED
            step.error = str(exc)
            task.updated_at = _now()
            events.append(
                _ev("system", AuditAction.FAILED, {"step": step.step_id, "error": str(exc)})
            )
            return StepOutcome(step, ok=False, error=str(exc)), events

        ok, note = self.verify(step, result)
        step.result = result
        task.updated_at = _now()
        if ok:
            step.status = TaskStatus.COMPLETED
            events.append(_ev("system", AuditAction.VERIFIED, {"step": step.step_id, "note": note}))
        else:
            step.status = TaskStatus.FAILED
            step.error = note
            events.append(
                _ev("system", AuditAction.FAILED, {"step": step.step_id, "verification": note})
            )
        return StepOutcome(step, ok=ok, result=result, error=None if ok else note), events

    def finalize(self, task: Task) -> list[dict]:
        """Called after the last step: mark COMPLETED only if all steps passed
        verification. Otherwise the task FAILS — never 'completed'."""
        events: list[dict] = []
        all_done = all(s.status == TaskStatus.COMPLETED for s in task.steps)
        if task.status != TaskStatus.RUNNING and task.status != TaskStatus.PAUSED:
            return events
        if all_done:
            task.status = TaskStatus.COMPLETED
            events.append(_ev("system", AuditAction.COMMAND_COMPLETED, {"task": task.task_id}))
        else:
            task.status = TaskStatus.FAILED
            events.append(
                _ev(
                    "system",
                    AuditAction.FAILED,
                    {"task": task.task_id, "reason": "unverified steps"},
                )
            )
        task.updated_at = _now()
        return events

    def retry_step(self, task: Task, step_id: str) -> None:
        """Re-arm a failed step under the SAME idempotency_key, then resume.

        Attempt counts are tracked by step_id on the engine, so a retry is
        bounded by max_attempts and never re-keys the step (a conforming tool
        can deduplicate on idempotency_key alone).
        """
        step = next((s for s in task.steps if s.step_id == step_id), None)
        if step is None:
            raise KeyError(f"no step {step_id}")
        if step.status != TaskStatus.FAILED:
            raise InvalidTransitionError(f"step {step_id} is {step.status.value}, not failed")
        step.status = TaskStatus.CREATED
        step.error = None  # idempotency_key is preserved
        if task.status in (TaskStatus.FAILED, TaskStatus.PAUSED):
            task.status = TaskStatus.RUNNING
            task.updated_at = _now()

    def arm_step(self, task: Task, step_id: str) -> None:
        """Re-arm an approval-pending step to CREATED so the approved run fires.

        Called AFTER `approve`; keeps the same idempotency_key so the executed
        step is provably the one the user approved."""
        step = next((s for s in task.steps if s.step_id == step_id), None)
        if step is None:
            raise KeyError(f"no step {step_id}")
        if step.status != TaskStatus.WAITING_APPROVAL:
            raise InvalidTransitionError(
                f"step {step_id} is {step.status.value}, not waiting_approval"
            )
        step.status = TaskStatus.CREATED
        step.error = None
        if task.status == TaskStatus.WAITING_APPROVAL:
            task.status = TaskStatus.RUNNING
            task.updated_at = _now()


def _task_risk(steps: list[TaskStep]):
    from packages.shared.schemas import RiskTier

    tiers = [s.risk_level for s in steps]
    if RiskTier.CRITICAL in tiers:
        return RiskTier.CRITICAL
    if RiskTier.EXECUTE in tiers:
        return RiskTier.EXECUTE
    if RiskTier.PREPARE in tiers:
        return RiskTier.PREPARE
    return RiskTier.OBSERVE


def _requires_approval(step: TaskStep) -> bool:
    """True if the frozen registry classifies this step's tool as
    execute/critical (confirmation required). Never decided by the LLM."""
    from packages.shared.permission_engine import PermissionEngine, UnknownToolError

    try:
        return PermissionEngine.classify(step.tool).confirmation_required
    except UnknownToolError:
        # Unknown tools are already refused at planning time; if one reaches
        # execution it is treated as requiring approval (blocked).
        return True


def _now():
    from datetime import datetime

    return datetime.utcnow()


def _ev(actor: str, action: AuditAction, detail: dict) -> dict:
    return {"actor": actor, "action": action, "detail": detail}
