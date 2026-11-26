"""Phase 9.1 — Celery/Redis background execution with a safe inline fallback.

The permission gate is the thing under test: a queued worker must re-check the
frozen registry and the approval ledger in its own process. Celery itself is
stubbed; no broker or worker is needed for these tests.
"""

from __future__ import annotations

import pytest

from packages.shared.infrastructure import TaskResult, TaskStatus as QueueStatus
from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase1_tools,
    register_phase2_tools,
)
from packages.shared.schemas import RiskTier, TaskStatus, TaskStep
from services.task_runner.app import background
from services.task_runner.app.api import run_task
from services.task_runner.app.manager import TaskManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    import services.task_runner.app.api as api

    PermissionEngine.reset()
    register_phase1_tools()
    register_phase2_tools()
    mgr = TaskManager(tmp_path / "tasks.db")
    monkeypatch.setattr(api, "get_manager", lambda: mgr)
    background.reset_queue()
    yield mgr
    mgr.close()
    PermissionEngine.reset()
    background.reset_queue()


class FakeQueue:
    _use_celery = True
    app = object()
    broker_url = "redis://localhost:6379/0"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.submitted: list[tuple] = []

    def submit(self, name, *args, task_id=None, **kwargs):
        self.submitted.append((name, args, task_id))
        if self.fail:
            return TaskResult(task_id=task_id, status=QueueStatus.FAILURE, error="broker down")
        return TaskResult(task_id=task_id, status=QueueStatus.RUNNING)


def make_task(mgr, tools=("system.open_app",), *, start=True):
    steps = []
    for tool in tools:
        tier = RiskTier.EXECUTE if tool == "system.send_message" else RiskTier.OBSERVE
        steps.append(
            TaskStep(
                agent="system",
                tool=tool,
                risk_level=tier,
                result={"args": {"app": "chrome", "recipient": "Disha", "content": "hi"}},
            )
        )
    task = mgr.create_task("background test", steps)
    if start:
        mgr.start(task.task_id)
    return task


class TestCeleryMode:
    def test_default_is_inline(self, monkeypatch):
        monkeypatch.delenv("VIORIS_TASK_QUEUE", raising=False)
        assert background.celery_mode() is False

    @pytest.mark.parametrize("value", ["1", "true", "celery", "redis", " CELERY "])
    def test_opt_in_values(self, monkeypatch, value):
        monkeypatch.setenv("VIORIS_TASK_QUEUE", value)
        assert background.celery_mode() is True

    def test_redis_url_alone_is_not_consent(self, monkeypatch):
        monkeypatch.delenv("VIORIS_TASK_QUEUE", raising=False)
        monkeypatch.setenv("VIORIS_REDIS_URL", "redis://localhost:6379/0")
        assert background.celery_mode() is False


class TestDispatch:
    def test_inline_mode_runs_the_step_locally(self, manager, monkeypatch):
        monkeypatch.delenv("VIORIS_TASK_QUEUE", raising=False)
        task = make_task(manager)
        seen = []

        def execute(step, task=None):
            seen.append(step.step_id)
            return {"ok": True}

        result = background.dispatch_step(task.task_id, run_local=execute, queue=FakeQueue())

        assert result["queued"] is False
        assert seen == [task.steps[0].step_id]
        assert manager.get(task.task_id).status == TaskStatus.COMPLETED

    def test_celery_mode_enqueues_and_audits_without_running_inline(
        self, manager, monkeypatch
    ):
        monkeypatch.setenv("VIORIS_TASK_QUEUE", "celery")
        task = make_task(manager)
        queue = FakeQueue()

        def execute(step, task=None):
            raise AssertionError("inline execution must not happen in celery mode")

        result = background.dispatch_step(task.task_id, run_local=execute, queue=queue)

        assert result["queued"] is True
        assert queue.submitted == [
            (
                background.RUN_TASK,
                (task.task_id,),
                f"vioris-{task.task_id}:{task.steps[0].step_id}",
            )
        ]
        assert manager.get(task.task_id).status == TaskStatus.RUNNING
        queued = [e for e in manager.audit_events_copy(limit=20) if e["action"] == "queued"]
        assert queued and queued[0]["task_id"] == task.task_id

    def test_enqueue_failure_never_falls_back_inline(self, manager, monkeypatch):
        monkeypatch.setenv("VIORIS_TASK_QUEUE", "celery")
        task = make_task(manager)

        def execute(step, task=None):
            raise AssertionError("a failed enqueue may already be on the broker")

        result = background.dispatch_step(
            task.task_id, run_local=execute, queue=FakeQueue(fail=True)
        )

        assert result["queued"] is False
        assert result["error"] == "broker down"
        assert manager.get(task.task_id).status == TaskStatus.RUNNING

    def test_no_runnable_step_is_not_enqueued(self, manager, monkeypatch):
        monkeypatch.setenv("VIORIS_TASK_QUEUE", "celery")
        task = make_task(manager, ("system.send_message",))
        # The first run pauses at the gate; it must never call the executor.
        manager.run_next_step(
            task.task_id, lambda step, task=None: pytest.fail("executed unapproved step")
        )
        queue = FakeQueue()

        result = background.dispatch_step(task.task_id, queue=queue)

        assert result["queued"] is False
        assert result["error"] == "no runnable step"
        assert queue.submitted == []

    def test_repeated_dispatch_is_deduplicated(self, manager, monkeypatch):
        monkeypatch.setenv("VIORIS_TASK_QUEUE", "celery")
        task = make_task(manager)
        queue = FakeQueue()

        first = background.dispatch_step(task.task_id, queue=queue)
        second = background.dispatch_step(task.task_id, queue=queue)

        assert first["queued"] is True
        assert second["duplicate"] is True
        assert len(queue.submitted) == 1


class TestWorker:
    def test_worker_drains_observe_steps(self, manager, monkeypatch):
        task = make_task(manager, ("system.open_app", "system.get_time"))
        seen = []

        def execute(step, task=None):
            seen.append(step.step_id)
            return {"ok": True}

        import services.task_runner.app.api as api

        monkeypatch.setattr(api, "_execute", execute)

        result = background.run_task(task.task_id)

        assert seen == [s.step_id for s in task.steps]
        assert result["status"] == "completed"
        assert result["ok"] is True

    def test_worker_stops_at_the_approval_gate(self, manager, monkeypatch):
        task = make_task(manager, ("system.send_message",))
        seen = []

        def execute(step, task=None):
            seen.append(step.step_id)
            return {"ok": True}

        import services.task_runner.app.api as api

        monkeypatch.setattr(api, "_execute", execute)

        result = background.run_task(task.task_id)

        assert seen == []
        assert result["status"] == "waiting_approval"
        assert manager.get(task.task_id).steps[0].status == TaskStatus.WAITING_APPROVAL


class TestApiSurface:
    def test_run_reports_queued_without_executing_locally(self, manager, monkeypatch):
        task = make_task(manager)
        monkeypatch.setattr(
            "services.task_runner.app.api.dispatch_step",
            lambda task_id: {
                "queued": True,
                "task": manager.get(task_id),
                "outcome": None,
            },
        )

        response = run_task(task.task_id)

        assert response["queued"] is True
        assert response["task"]["status"] == "running"
        assert response["step"]["step_id"] is None
        assert response["step"]["ok"] is False
