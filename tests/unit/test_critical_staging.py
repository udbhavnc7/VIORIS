"""Reversible-by-default Critical execution wired into the task runner.

The approval gate still decides *whether* an action may run. Staging only adds
a cancel window (and a real reversal handler where one exists) before the
already-approved step fires.
"""

import threading
import time

import pytest

from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase2_tools,
    register_phase3_files_tools,
)
from packages.shared.schemas import RiskTier, TaskStatus, TaskStep
from services.task_runner.app.manager import TaskManager


@pytest.fixture
def manager(tmp_path):
    PermissionEngine.reset()
    register_phase2_tools()
    register_phase3_files_tools()
    mgr = TaskManager(tmp_path / "staged.db")
    yield mgr
    mgr.close()
    PermissionEngine.reset()


def _wait_for_status(action, status, timeout=2.0):
    deadline = time.monotonic() + timeout
    while action.status.value != status and time.monotonic() < deadline:
        time.sleep(0.01)
    return action.status.value == status


def _critical_task(mgr, tool="system.register_payment"):
    step = TaskStep(
        agent="system",
        tool=tool,
        risk_level=RiskTier.CRITICAL,
        result={"args": {"payee": "Landlord", "amount": 1200, "currency": "INR"}},
    )
    task = mgr.create_task("critical staging test", [step])
    mgr.start(task.task_id)
    return task, task.steps[0]


class TestManagerStaging:
    def test_stage_records_audit_and_listing(self, manager):
        task, step = _critical_task(manager)
        action = manager.stage_critical(
            task.task_id,
            step.step_id,
            step.tool,
            "pay landlord",
            {"payee": "Landlord", "amount": 1200},
            execute_fn=lambda: {"ok": True},
            execute_after_seconds=60,
        )

        assert action.status.value == "staged"
        assert [s["stage_id"] for s in manager.list_stages()] == [action.stage_id]
        actions = [e["action"] for e in manager.audit_events_copy()]
        assert "staged" in actions

    def test_cancel_prevents_execution_and_cancels_task(self, manager):
        task, step = _critical_task(manager)
        executed = threading.Event()
        action = manager.stage_critical(
            task.task_id,
            step.step_id,
            step.tool,
            "pay landlord",
            {},
            execute_fn=lambda: executed.set(),
            execute_after_seconds=0.2,
        )

        manager.cancel_staged(action.stage_id)
        executed.wait(timeout=0.5)

        assert not executed.is_set()
        assert action.status.value == "cancelled"
        assert manager.get(task.task_id).status == TaskStatus.CANCELLED
        actions = [e["action"] for e in manager.audit_events_copy()]
        assert "stage_cancelled" in actions

    def test_window_expiry_executes_and_audits(self, manager):
        task, step = _critical_task(manager)
        executed = threading.Event()

        action = manager.stage_critical(
            task.task_id,
            step.step_id,
            step.tool,
            "pay landlord",
            {},
            execute_fn=lambda: executed.set(),
            execute_after_seconds=0.05,
        )
        assert executed.wait(timeout=2)
        assert _wait_for_status(action, "executed")

        actions = [e["action"] for e in manager.audit_events_copy()]
        assert "stage_executed" in actions

    def test_undo_calls_reversal_handler(self, manager):
        task, step = _critical_task(manager)
        undone = threading.Event()

        action = manager.stage_critical(
            task.task_id,
            step.step_id,
            step.tool,
            "delete file",
            {},
            execute_fn=lambda: None,
            undo_fn=undone.set,
            execute_after_seconds=0.05,
            undo_window_seconds=5,
        )
        assert _wait_for_status(action, "executed")

        result = manager.undo_staged(action.stage_id)

        assert result["status"] == "undone"
        assert undone.is_set()
        actions = [e["action"] for e in manager.audit_events_copy()]
        assert "undone" in actions

    def test_undo_without_handler_is_refused(self, manager):
        task, step = _critical_task(manager)
        action = manager.stage_critical(
            task.task_id,
            step.step_id,
            step.tool,
            "pay landlord",
            {},
            execute_fn=lambda: None,
            execute_after_seconds=0.05,
            undo_window_seconds=5,
        )
        assert _wait_for_status(action, "executed")

        with pytest.raises(ValueError, match="not undoable"):
            manager.undo_staged(action.stage_id)
