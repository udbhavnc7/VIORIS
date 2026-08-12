"""End-to-end approval flow driven through the TaskManager + CLI surface.

Covers the Prompt 2.3 exit test: an execute/critical step is never fired
without an explicit approve against its idempotency key; the diff card shows
only registry-declared fields; and reject abandons the step forever.
"""


from packages.shared.permission_engine import PermissionEngine, register_phase1_tools
from packages.shared.permission_engine import register_phase2_tools
from packages.shared.schemas import TaskStatus, TaskStep

from services.task_runner.app.cli import default_execute
from services.task_runner.app.manager import TaskManager

register_phase1_tools()
register_phase2_tools()


def test_cli_end_to_end_approve(tmp_path):
    PermissionEngine.reset()
    register_phase1_tools()
    register_phase2_tools()
    db = tmp_path / "t.db"
    mgr = TaskManager(db)
    steps = [
        TaskStep(
            agent="system",
            tool="system.send_message",
            risk_level="execute",
            result={"args": {"recipient": "alice", "content": "hi", "channel": "sms"}},
        )
    ]
    task = mgr.create_task("send nudge", steps)
    mgr.start(task.task_id)

    # 1. run pauses; nothing executed yet
    task, outcome = mgr.run_next_step(task.task_id, default_execute)
    assert outcome.error == "awaiting approval"
    assert task.status == TaskStatus.WAITING_APPROVAL
    assert mgr.pending_approvals(task.task_id)

    # 2. diff card shows only registered fields, with values
    just = {a["tool"]: a["diff_card"] for a in mgr.pending_approvals()}
    assert just["system.send_message"] == {
        "recipient": "alice",
        "content": "hi",
        "channel": "sms",
    }

    # 3. approve, then run fires exactly once (same idempotency_key)
    approval_id = mgr.pending_approvals(task.task_id)[0]["approval_id"]
    task = mgr.approve(task.task_id, approval_id)
    calls = []
    task, outcome = mgr.run_next_step(task.task_id, lambda s, task=None: (calls.append(s.idempotency_key), {"ok": True})[1])
    assert task.status == TaskStatus.COMPLETED
    assert len(calls) == 1


def test_cli_end_to_end_reject(tmp_path):
    PermissionEngine.reset()
    register_phase1_tools()
    register_phase2_tools()
    db = tmp_path / "t.db"
    mgr = TaskManager(db)
    steps = [
        TaskStep(
            agent="system",
            tool="system.register_payment",
            risk_level="critical",
            result={"args": {"payee": "mom", "currency": "usd", "amount": 30, "account": "checking"}},
        )
    ]
    task = mgr.create_task("pay mom", steps)
    mgr.start(task.task_id)
    mgr.run_next_step(task.task_id, default_execute)
    approval_id = mgr.pending_approvals(task.task_id)[0]["approval_id"]
    assert mgr.pending_approvals()[0]["diff_card"] == {
        "payee": "mom",
        "currency": "usd",
        "amount": 30,
        "account": "checking",
    }

    calls = []
    task = mgr.reject(task.task_id, approval_id)
    assert task.status == TaskStatus.CANCELLED
    _, outcome = mgr.run_next_step(task.task_id, lambda s, task=None: (calls.append(s.idempotency_key), {"ok": True})[1])
    assert outcome.step is None or outcome.error  # rejected step never fires
    assert calls == []