"""Critical approvals stage with a cancel window instead of firing at once."""

import time

import pytest
from fastapi.testclient import TestClient

from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase2_tools,
    register_phase3_files_tools,
)
from packages.shared.schemas import RiskTier, TaskStep
from services.task_runner.app.api import app as tr_app
from services.task_runner.app.manager import TaskManager


@pytest.fixture
def tr_client(tmp_path, monkeypatch):
    import services.task_runner.app.api as api

    PermissionEngine.reset()
    register_phase2_tools()
    register_phase3_files_tools()
    mgr = TaskManager(tmp_path / "staged_api.db")
    monkeypatch.setattr(api, "get_manager", lambda: mgr)
    with TestClient(tr_app) as c:
        yield c, mgr, api
    # Stop any daemon timers before the patched get_manager is restored, so a
    # late stage cannot dispatch against the production manager.
    for stage in mgr.list_stages():
        if stage["status"] == "staged":
            try:
                mgr.cancel_staged(stage["stage_id"])
            except (KeyError, ValueError):
                pass
    mgr.close()
    PermissionEngine.reset()


def _waiting_critical(mgr):
    step = TaskStep(
        agent="system",
        tool="system.register_payment",
        risk_level=RiskTier.CRITICAL,
        result={"args": {"payee": "Landlord", "amount": 1200, "currency": "INR"}},
    )
    task = mgr.create_task("pay landlord", [step])
    mgr.start(task.task_id)
    task, _outcome = mgr.run_next_step(task.task_id, lambda step, task: {"ok": True})
    approval = mgr.pending_approvals(task.task_id)[0]
    return task, approval


def _allow_critical_approval(monkeypatch, api):
    monkeypatch.setattr(api, "CRITICAL_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(api, "verify_second_factor", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(api, "load_or_create_key", lambda: b"test-key")


def test_critical_approval_stages_and_can_be_cancelled(tr_client, monkeypatch):
    client, mgr, api = tr_client
    _allow_critical_approval(monkeypatch, api)
    dispatched: list[str] = []
    monkeypatch.setattr(
        api,
        "dispatch_step",
        lambda task_id: dispatched.append(task_id) or {"queued": False, "task": mgr.get(task_id)},
    )
    task, approval = _waiting_critical(mgr)

    r = client.post(
        f"/approvals/{approval['approval_id']}/approve",
        headers={"X-Vioris-Second-Factor": "proof"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["staged"] is True
    assert body["stage_id"]
    assert dispatched == [], "a staged Critical action must not fire immediately"

    stages = client.get("/stages").json()
    assert [s["stage_id"] for s in stages] == [body["stage_id"]]

    cancelled = client.post(f"/stages/{body['stage_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert dispatched == []
    assert mgr.get(task.task_id).status.value == "cancelled"


def test_staged_critical_fires_after_window(tr_client, monkeypatch):
    client, mgr, api = tr_client
    _allow_critical_approval(monkeypatch, api)
    monkeypatch.setenv("VIORIS_CRITICAL_STAGE_SECONDS", "0.05")
    dispatched: list[str] = []
    monkeypatch.setattr(
        api,
        "dispatch_step",
        lambda task_id: dispatched.append(task_id) or {"queued": False, "task": mgr.get(task_id)},
    )
    task, approval = _waiting_critical(mgr)

    r = client.post(
        f"/approvals/{approval['approval_id']}/approve",
        headers={"X-Vioris-Second-Factor": "proof"},
    )
    assert r.json()["staged"] is True

    deadline = time.monotonic() + 2
    while not dispatched and time.monotonic() < deadline:
        time.sleep(0.02)

    assert dispatched == [task.task_id]
    actions = [e["action"] for e in mgr.audit_events_copy()]
    assert "staged" in actions
    assert "stage_executed" in actions


def test_run_is_refused_while_stage_is_pending(tr_client, monkeypatch):
    client, mgr, api = tr_client
    _allow_critical_approval(monkeypatch, api)
    task, approval = _waiting_critical(mgr)

    r = client.post(
        f"/approvals/{approval['approval_id']}/approve",
        headers={"X-Vioris-Second-Factor": "proof"},
    )
    stage_id = r.json()["stage_id"]

    ran = client.post(f"/tasks/{task.task_id}/run").json()

    assert ran["staged"] is True
    assert ran["step"]["ok"] is False
    assert mgr.get(task.task_id).steps[0].status.value == "created"

    client.post(f"/stages/{stage_id}/cancel")
