from pathlib import Path

import pytest

from packages.shared.permission_engine import PermissionEngine, register_phase1_tools
from packages.shared.permission_engine import register_phase2_tools
from packages.shared.schemas import RiskTier, TaskStatus, TaskStep

from services.task_runner.app.manager import TaskManager

register_phase1_tools()
register_phase2_tools()


@pytest.fixture(autouse=True)
def full_registry():
    PermissionEngine.reset()
    register_phase1_tools()
    register_phase2_tools()
    yield
    PermissionEngine.reset()


def exec_step(tool="system.send_message", risk=RiskTier.EXECUTE, **args) -> TaskStep:
    return TaskStep(agent="system", tool=tool, risk_level=risk, result={"args": args})

def record_calls(ledger=None):
    def _execute(step):
        if ledger is not None:
            ledger.append(step.idempotency_key)
        return {"ok": True}
    return _execute


class TestApprovalGate:
    def test_execute_step_pauses_before_running(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        ledger = []
        task = mgr.create_task(
            "send nudge",
            [exec_step("system.send_message", recipient="alice", content="hi", channel="sms")],
        )
        mgr.start(task.task_id)
        task, outcome = mgr.run_next_step(task.task_id, record_calls(ledger))

        assert outcome.step.status == TaskStatus.WAITING_APPROVAL
        assert ledger == []  # NOT executed
        pending = mgr.pending_approvals(task.task_id)
        assert len(pending) == 1
        assert pending[0]["tool"] == "system.send_message"
        assert pending[0]["diff_card"] == {"recipient": "alice", "content": "hi", "channel": "sms"}

    def test_observe_step_never_requires_approval(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        task = mgr.create_task("time", [TaskStep(agent="system", tool="system.get_time", risk_level="observe")])
        mgr.start(task.task_id)
        task, outcome = mgr.run_next_step(task.task_id, record_calls([]))
        assert outcome.ok
        assert outcome.step.status == TaskStatus.COMPLETED
        assert mgr.pending_approvals(task.task_id) == []

    def test_approval_diff_card_uses_registered_fields_only(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        task = mgr.create_task(
            "pay", [exec_step("system.register_payment", payee="mom", currency="usd", amount=30, account="checking")]
        )
        mgr.start(task.task_id)
        mgr.run_next_step(task.task_id, record_calls([]))
        pending = mgr.pending_approvals(task.task_id)
        assert pending[0]["tool"] == "system.register_payment"
        assert pending[0]["diff_card"] == {"payee": "mom", "currency": "usd", "amount": 30, "account": "checking"}


class TestApproveFlow:
    def test_approve_then_run_uses_same_idempotency_key(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        calls = []
        task = mgr.create_task(
            "send", [exec_step("system.send_message", recipient="bob", content="hi", channel="sms")]
        )
        mgr.start(task.task_id)
        task, outcome = mgr.run_next_step(task.task_id, record_calls(calls))
        pending = mgr.pending_approvals(task.task_id)
        key_before = outcome.step.idempotency_key

        task = mgr.approve(task.task_id, pending[0]["approval_id"])
        assert task.status == TaskStatus.RUNNING
        task, outcome = mgr.run_next_step(task.task_id, record_calls(calls))
        assert outcome.step.status == TaskStatus.COMPLETED
        assert outcome.step.idempotency_key == key_before  # same key the approval was tied to
        assert calls == [key_before]  # fired exactly once

    def test_reject_abandons_step_never_fires(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        calls = []
        task = mgr.create_task("send", [exec_step("system.send_message", recipient="bob", content="hi", channel="sms")])
        mgr.start(task.task_id)
        mgr.run_next_step(task.task_id, record_calls(calls))
        pending = mgr.pending_approvals(task.task_id)
        task = mgr.reject(task.task_id, pending[0]["approval_id"])
        assert task.status == TaskStatus.CANCELLED
        assert calls == []  # never executed
        assert task.steps[0].status == TaskStatus.CANCELLED

    def test_stale_approval_cannot_fire_a_new_run(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        calls = []
        task = mgr.create_task("send", [exec_step("system.send_message", recipient="bob", content="hi", channel="sms")])
        mgr.start(task.task_id)
        mgr.run_next_step(task.task_id, record_calls(calls))
        approval_id = mgr.pending_approvals(task.task_id)[0]["approval_id"]

        # Same task cleared + re-planned under a NEW idempotency key, but the
        # old approval still exists. It must NOT authorize the new step.
        new_step = exec_step("system.send_message", recipient="bob", content="hi", channel="sms")
        task2 = mgr.create_task("send again", [new_step])
        mgr.start(task2.task_id)
        with pytest.raises(ValueError, match="does not belong"):
            mgr.approve(task2.task_id, approval_id)
        assert calls == []  # nothing fired


class TestCritical:
    def test_critical_steps_always_pause(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "t.db")
        task = mgr.create_task("pay", [exec_step("system.register_payment", payee="mom", currency="usd", amount=30, account="checking")])
        mgr.start(task.task_id)
        task, outcome = mgr.run_next_step(task.task_id, record_calls([]))
        assert outcome.step.status == TaskStatus.WAITING_APPROVAL
        assert task.status == TaskStatus.WAITING_APPROVAL