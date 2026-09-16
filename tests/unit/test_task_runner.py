from pathlib import Path

import pytest

from packages.shared.permission_engine import PermissionEngine, register_phase1_tools
from packages.shared.schemas import RiskTier, TaskStatus, TaskStep
from services.task_runner.app.engine import InvalidTransitionError, TaskEngine
from services.task_runner.app.manager import TaskManager


@pytest.fixture(autouse=True)
def _phase1_registry():
    """Engine tests exercise observe-tier Phase 1 tools (open_app/get_time).
    Register them so the static registry classifies steps as OBSERVE and the
    steps run without pausing for approval — the approval gate is tested in
    test_approvals.py against the execute/critical Phase 2 tools."""
    PermissionEngine.reset()
    register_phase1_tools()
    yield
    PermissionEngine.reset()


def step(tool="system.open_app", risk=RiskTier.OBSERVE, agent="computer") -> TaskStep:
    return TaskStep(agent=agent, tool=tool, risk_level=risk)


def ok_execute(step: TaskStep, task=None) -> dict:
    return {"ok": True, "tool": step.tool}


class FailingExecute:
    """Raises until told otherwise; records every call keyed by idempotency_key."""

    def __init__(self, fail_until_calls=1):
        self.fail_until_calls = fail_until_calls
        self.seen: list[tuple[str, str]] = []  # (step_id, idempotency_key)

    def __call__(self, step: TaskStep, task=None) -> dict:
        self.seen.append((step.step_id, step.idempotency_key))
        if len(self.seen) <= self.fail_until_calls:
            raise RuntimeError("boom")
        return {"ok": True}


class TestLifecycle:
    def test_create_start_pause_resume(self):
        eng = TaskEngine()
        task = eng.create_task("do a thing", [step(), step()])
        assert task.status == TaskStatus.CREATED
        eng.start(task)
        assert task.status == TaskStatus.RUNNING
        eng.pause(task)
        assert task.status == TaskStatus.PAUSED
        eng.resume(task)
        assert task.status == TaskStatus.RUNNING

    def test_invalid_transition_raises(self):
        eng = TaskEngine()
        task = eng.create_task("do a thing", [step()])
        with pytest.raises(InvalidTransitionError):
            eng.pause(task)  # CREATED -> PAUSED not allowed

    def test_complete_only_after_verification(self):
        eng = TaskEngine()
        task = eng.create_task("t", [step(), step()])
        eng.start(task)
        o, _ = eng.run_next_step(task, ok_execute)
        assert o.ok and o.step.status == TaskStatus.COMPLETED
        o, _ = eng.run_next_step(task, ok_execute)
        assert o.ok and o.step.status == TaskStatus.COMPLETED
        assert all(s.status == TaskStatus.COMPLETED for s in task.steps)
        eng.finalize(task)
        assert task.status == TaskStatus.COMPLETED

    def test_task_never_completed_on_unverified_step(self):
        eng = TaskEngine(verify=lambda _s, _r: (False, "could not confirm"))
        task = eng.create_task("t", [step()])
        eng.start(task)
        o, _ = eng.run_next_step(task, ok_execute)
        assert not o.ok  # tool succeeded but verification failed
        assert o.step.status == TaskStatus.FAILED
        eng.finalize(task)
        assert task.status == TaskStatus.FAILED
        assert o.step.error == "could not confirm"

    def test_cancel_marks_outstanding_steps(self):
        eng = TaskEngine()
        task = eng.create_task("t", [step(), step()])
        eng.start(task)
        eng.run_next_step(task, ok_execute)
        eng.cancel(task)
        assert task.status == TaskStatus.CANCELLED
        assert task.steps[1].status == TaskStatus.CANCELLED


class TestRetry:
    def test_retry_same_idempotency_key_no_duplicated_side_effect(self):
        eng = TaskEngine(max_attempts=3)
        fx = FailingExecute(fail_until_calls=1)
        task = eng.create_task("t", [step(tool="system.get_time")])
        eng.start(task)

        o, _ = eng.run_next_step(task, fx)
        assert not o.ok
        first_key = fx.seen[0][1]
        eng.retry_step(task, o.step.step_id)
        o2, _ = eng.run_next_step(task, fx)
        assert o2.ok
        assert o.step.idempotency_key == first_key == o2.step.idempotency_key
        assert len(fx.seen) == 2  # ran twice under ONE logically-equal key

    def test_retry_clears_error(self):
        eng = TaskEngine()
        fx = FailingExecute(fail_until_calls=1)
        task = eng.create_task("t", [step()])
        eng.start(task)
        o, _ = eng.run_next_step(task, fx)
        assert o.step.error == "boom"
        eng.retry_step(task, o.step.step_id)
        assert o.step.status == TaskStatus.CREATED
        assert o.step.error is None

    def test_max_attempts_fails_task(self):
        eng = TaskEngine(max_attempts=2)
        fx = FailingExecute(fail_until_calls=99)
        task = eng.create_task("t", [step()])
        eng.start(task)

        # Attempt 1: fails, retry
        o, _ = eng.run_next_step(task, fx)
        assert not o.ok
        eng.retry_step(task, o.step.step_id)
        # Attempt 2: fails, retry
        o, _ = eng.run_next_step(task, fx)
        assert not o.ok
        eng.retry_step(task, o.step.step_id)
        # Attempt 3: at max_attempts -> task fails
        o, _ = eng.run_next_step(task, fx)
        assert o.error == "max attempts exceeded"
        assert task.status == TaskStatus.FAILED


class TestManager:
    def test_pause_resume_via_manager(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        task = mgr.create_task("open vs code", [step(), step()])
        mgr.start(task.task_id)
        mgr.run_next_step(task.task_id, ok_execute)
        task = mgr.pause(task.task_id)
        assert task.status == TaskStatus.PAUSED
        task = mgr.resume(task.task_id)
        assert task.status == TaskStatus.RUNNING

    def test_task_persists_across_restart(self, tmp_path):
        db = Path(tmp_path) / "t.db"
        mgr = TaskManager(db)
        task = mgr.create_task("keep me", [step()])
        mgr.start(task.task_id)
        mgr.close()

        mgr2 = TaskManager(db)
        reloaded = mgr2.get(task.task_id)
        assert reloaded.status == TaskStatus.RUNNING
        assert reloaded.request == "keep me"

    def test_audit_chain_intact_after_retry(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        fx = FailingExecute(fail_until_calls=1)
        task = mgr.create_task("t", [step(tool="system.get_time")])
        mgr.start(task.task_id)
        _, o = mgr.run_next_step(task.task_id, fx)
        mgr.retry_step(task.task_id, o.step.step_id)
        mgr.run_next_step(task.task_id, fx)
        ok, problems = mgr.verify_chain()
        assert ok, problems
        actions = [e["action"] for e in mgr.audit_events_copy()]
        assert any(actions.count(a) > 0 for a in ("verified", "failed", "planned_step"))

    def test_task_completes_only_after_all_steps_verified(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        task = mgr.create_task("multi", [step(), step(), step()])
        mgr.start(task.task_id)
        for _ in range(3):
            _, o = mgr.run_next_step(task.task_id, ok_execute)
            assert o.ok  # each step verifies
        final = mgr.get(task.task_id)
        assert final.status == TaskStatus.COMPLETED
        assert all(s.status == TaskStatus.COMPLETED for s in final.steps)


def step_step(name: str = "step") -> TaskStep:
    return TaskStep(agent="computer", tool=name, risk_level=RiskTier.OBSERVE)


def test_task_risk_rolls_up_to_highest_tier():
    eng = TaskEngine()
    task = eng.create_task(
        "t",
        [step(risk="observe"), TaskStep(agent="a", tool="x", risk_level=RiskTier.CRITICAL)],
    )
    assert task.risk_level == RiskTier.CRITICAL
