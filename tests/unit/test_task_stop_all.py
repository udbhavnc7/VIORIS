"""Task-runner: stop-everything + global approvals (Phase 5, Prompt 5.2)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from packages.shared.permission_engine import register_phase1_tools, register_phase2_tools
from packages.shared.schemas import RiskTier, TaskStep
from services.task_runner.app.api import app as tr_app
from services.task_runner.app.manager import TaskManager


@pytest.fixture
def client(tmp_path):
    register_phase1_tools()
    register_phase2_tools()
    import services.task_runner.app.api as tr_api

    tr_api._manager = TaskManager(tmp_path / "tr.db")
    with TestClient(tr_app) as c:
        yield c


def _create_execute_task(tmp_path, request="approve me") -> str:
    out = Path(tmp_path) / "hold.db"
    mgr = TaskManager(out)
    step = TaskStep(
        agent="computer",
        tool="system.send_message",
        risk_level=RiskTier.EXECUTE,
        result={"args": {"recipient": "ritesh", "content": "hi", "channel": "whatsapp"}},
    )
    task = mgr.create_task(request, [step])
    mgr.run_next_step(task.task_id, lambda s, task=None: {"ok": True})
    return mgr, task.task_id


def test_stop_everything_halts_and_blocks_late_approve(tmp_path):
    mgr, task_id = _create_execute_task(tmp_path)
    assert mgr.get(task_id).status.value == "waiting_approval"
    approval = mgr.pending_approvals(task_id)[0]

    affected = mgr.stop_all()
    assert task_id in affected

    stopped = mgr.get(task_id)
    assert stopped.status.value == "stopped"
    step = stopped.steps[0]
    assert step.status.value == "cancelled"

    # the pending approval is defeated by stop — queue is empty
    assert mgr.pending_approvals(task_id) == []

    # and approving must not re-arm the step
    register_phase1_tools()
    register_phase2_tools()
    with pytest.raises(Exception):
        mgr.approve(task_id, approval["approval_id"])


def test_stop_everything_ignores_terminal_tasks(tmp_path):
    register_phase1_tools()
    register_phase2_tools()
    from packages.shared.permission_engine import PermissionEngine

    PermissionEngine.freeze()
    mgr = TaskManager(Path(tmp_path) / "s.db")
    step = TaskStep(agent="computer", tool="system.open_app", risk_level=RiskTier.OBSERVE)
    done = mgr.create_task("done", [step])
    mgr.run_next_step(done.task_id, lambda s, task=None: {"ok": True})

    assert mgr.get(done.task_id).status.value == "completed"
    assert mgr.stop_all() == []


def test_global_approvals_and_stop_endpoints(client, tmp_path):
    import services.task_runner.app.api as tr_api

    mgr, task_id = _create_execute_task(tmp_path)
    tr_api._manager = mgr

    approvals = client.get("/approvals").json()
    assert len(approvals) == 1 and approvals[0]["task_id"] == task_id
    assert approvals[0]["tool"] == "system.send_message"
    assert "recipient" in approvals[0]["diff_card"]

    r = client.post("/stop-everything")
    assert r.status_code == 200
    assert r.json()["count"] == 1

    assert client.get("/approvals").json() == []