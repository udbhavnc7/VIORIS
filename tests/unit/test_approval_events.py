import asyncio

import pytest

from packages.shared.permission_engine import PermissionEngine, register_phase1_tools
from packages.shared.permission_engine import register_phase2_tools
from packages.shared.schemas import TaskStep

from services.task_runner.app.events import EventHub
from services.task_runner.app.manager import TaskManager

register_phase1_tools()
register_phase2_tools()


@pytest.fixture(autouse=True)
def _full_registry():
    PermissionEngine.reset()
    register_phase1_tools()
    register_phase2_tools()
    yield
    PermissionEngine.reset()


def exec_step(**args) -> TaskStep:
    return TaskStep(
        agent="system",
        tool="system.send_message",
        risk_level="execute",
        result={"args": args},
    )


class TestEventHub:
    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber_in_order(self):
        hub = EventHub()
        sub = hub.subscribe(asyncio.get_running_loop())

        hub.publish({"type": "pending_approval", "n": 1})
        hub.publish({"type": "approved", "n": 2})

        got = [await asyncio.wait_for(sub.queue.get(), 1), await asyncio.wait_for(sub.queue.get(), 1)]
        assert [g["type"] for g in got] == ["pending_approval", "approved"]
        assert got[0] == {"type": "pending_approval", "n": 1}

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        hub = EventHub()
        sub = hub.subscribe(asyncio.get_running_loop())
        hub.unsubscribe(sub)

        hub.publish({"type": "approved"})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub.queue.get(), timeout=0.05)


class TestApprovalEvents:
    def test_run_pauses_and_emits_pending_approval(self, tmp_path):
        mgr = TaskManager(tmp_path / "t.db")
        task = mgr.create_task("send", [exec_step(recipient="alice", content="hi", channel="sms")])
        mgr.start(task.task_id)
        task, _ = mgr.run_next_step(task.task_id, lambda s: {"ok": True})
        pending = mgr.pending_approvals(task.task_id)
        assert pending and pending[0]["tool"] == "system.send_message"
        assert pending[0]["diff_card"] == {"recipient": "alice", "content": "hi", "channel": "sms"}

    def test_approve_and_reject_record_resolution(self, tmp_path):
        mgr = TaskManager(tmp_path / "t.db")
        task = mgr.create_task("send", [exec_step(recipient="bob", content="hi", channel="sms")])
        mgr.start(task.task_id)
        mgr.run_next_step(task.task_id, lambda s: {"ok": True})
        approval_id = mgr.pending_approvals(task.task_id)[0]["approval_id"]

        task = mgr.approve(task.task_id, approval_id)
        assert mgr.approvals.get(approval_id).status == "approved"
        assert mgr.pending_approvals(task.task_id) == []

        # A fresh task -> fresh pending approval -> reject resolves it
        task2 = mgr.create_task("send2", [exec_step(recipient="bob", content="hi", channel="sms")])
        mgr.start(task2.task_id)
        mgr.run_next_step(task2.task_id, lambda s: {"ok": True})
        aid2 = mgr.pending_approvals(task2.task_id)[0]["approval_id"]
        mgr.reject(task2.task_id, aid2)
        assert mgr.approvals.get(aid2).status == "rejected"