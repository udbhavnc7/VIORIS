"""Tests for Phase 8.6 — idempotency checks on task retries."""

import pytest

from packages.shared.permission_engine import PermissionEngine, register_phase1_tools, register_phase2_tools
from packages.shared.schemas import RiskTier, TaskStatus, TaskStep
from services.task_runner.app.approvals import ApprovalStore
from services.task_runner.app.engine import TaskEngine


@pytest.fixture(autouse=True)
def _registry():
    PermissionEngine.reset()
    register_phase1_tools()
    register_phase2_tools()
    yield
    PermissionEngine.reset()


@pytest.fixture
def approval_store(tmp_path):
    return ApprovalStore(tmp_path / "approvals.db")


@pytest.fixture
def engine(approval_store):
    return TaskEngine(approval_store=approval_store, max_attempts=3)


def _make_step(tool: str, tier: RiskTier = RiskTier.OBSERVE) -> TaskStep:
    return TaskStep(agent="test", tool=tool, risk_level=tier)


class TestRetryIdempotency:
    def test_retry_observe_step_preserves_idempotency_key(self, engine):
        """Observe-tier retries keep the same idempotency_key (no approval needed)."""
        step = _make_step("system.get_time")
        task = engine.create_task("test", [step])
        engine.start(task)
        old_key = step.idempotency_key

        # Simulate failure
        step.status = TaskStatus.FAILED
        task.status = TaskStatus.FAILED

        # Retry
        events = engine.retry_step(task, step.step_id)
        assert step.status == TaskStatus.CREATED
        assert step.idempotency_key == old_key  # unchanged for observe
        assert step.error is None

    def test_retry_execute_step_invalidates_old_approval(self, engine, approval_store):
        """Execute-tier retries invalidate any stale approval."""
        step = _make_step("system.send_message", RiskTier.EXECUTE)
        task = engine.create_task("test", [step])
        engine.start(task)
        old_key = step.idempotency_key

        # Simulate: step ran, failed approval, now retrying
        step.status = TaskStatus.FAILED
        task.status = TaskStatus.FAILED

        # Retry — should get new idempotency key and invalidation event
        events = engine.retry_step(task, step.step_id)
        assert step.status == TaskStatus.CREATED
        assert step.idempotency_key != old_key  # new key for re-approval
        assert step.error is None

        # Should have an audit event about the invalidation
        invalidation_events = [e for e in events if e["action"].value == "requested_approval"]
        assert len(invalidation_events) == 1
        assert invalidation_events[0]["detail"]["reason"] == "retry — previous approval invalidated"

    def test_retry_critical_step_gets_new_idempotency_key(self, engine):
        """Critical-tier retries get a fresh idempotency_key."""
        step = _make_step("system.register_payment", RiskTier.CRITICAL)
        task = engine.create_task("test", [step])
        engine.start(task)
        old_key = step.idempotency_key

        step.status = TaskStatus.FAILED
        task.status = TaskStatus.FAILED

        events = engine.retry_step(task, step.step_id)
        assert step.idempotency_key != old_key

    def test_retry_requires_previous_failure(self, engine):
        """Cannot retry a step that hasn't failed."""
        step = _make_step("system.get_time")
        task = engine.create_task("test", [step])
        engine.start(task)

        from services.task_runner.app.engine import InvalidTransitionError
        with pytest.raises(InvalidTransitionError):
            engine.retry_step(task, step.step_id)

    def test_retry_step_not_found(self, engine):
        """Cannot retry a nonexistent step."""
        step = _make_step("system.get_time")
        task = engine.create_task("test", [step])
        engine.start(task)

        with pytest.raises(KeyError):
            engine.retry_step(task, "nonexistent_step")

    def test_old_approval_not_valid_for_new_key(self, engine, approval_store):
        """After retry, the old approval cannot be used with the new idempotency_key."""
        step = _make_step("system.send_message", RiskTier.EXECUTE)
        task = engine.create_task("test", [step])
        engine.start(task)
        old_key = step.idempotency_key

        # Create an approval with the old key
        approval = approval_store.create(
            task.task_id, step.step_id, old_key, step.tool, {}
        )
        approval_store.approve(approval)

        # Verify old approval works
        assert approval_store.is_approved_for_step(
            task.task_id, step.step_id, old_key
        )

        # Retry the step — old approval should be invalidated
        step.status = TaskStatus.FAILED
        task.status = TaskStatus.FAILED
        engine.retry_step(task, step.step_id)

        # Old approval should no longer be valid
        assert not approval_store.is_approved_for_step(
            task.task_id, step.step_id, old_key
        )

        # New key has no approval yet
        assert not approval_store.is_approved_for_step(
            task.task_id, step.step_id, step.idempotency_key
        )

    def test_retry_returns_events_list(self, engine):
        """retry_step returns a list of audit events."""
        step = _make_step("system.get_time")
        task = engine.create_task("test", [step])
        engine.start(task)
        step.status = TaskStatus.FAILED
        task.status = TaskStatus.FAILED

        events = engine.retry_step(task, step.step_id)
        assert isinstance(events, list)
        # Observe tier: no invalidation event
        assert len(events) == 0


class TestRetryBoundaries:
    def test_max_attempts_enforced(self, engine):
        """Step fails permanently after max_attempts retries."""
        step = _make_step("system.get_time")
        task = engine.create_task("test", [step])
        engine.start(task)

        def failing_execute(s, t):
            raise RuntimeError("tool error")

        # Run and fail 3 times, retrying each time to re-arm the step
        for attempt in range(3):
            outcome, events = engine.run_next_step(task, failing_execute)
            assert outcome.ok is False
            assert step.status == TaskStatus.FAILED
            engine.retry_step(task, step.step_id)
            assert step.status == TaskStatus.CREATED

        # 4th run: attempt count is 3 >= max_attempts(3), should fail permanently
        outcome, events = engine.run_next_step(task, failing_execute)
        assert outcome.ok is False
        assert "max attempts" in outcome.error
        assert task.status == TaskStatus.FAILED
