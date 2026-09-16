"""Phase 6 exit e2e: smart-home connector driven through the real task loop.

Boots the smart-home connector API and the task-runner as real uvicorn
services sharing ONE approval ledger (task_runner.db), then drives the full
Phase 6 loop exactly as the desktop/orchestrator does:

    plan smart_home.control (execute) -> run -> the step pauses at the
    approval gate with a diff card -> approve -> the task-runner executor
    dispatches to the connector over HTTP -> the connector re-verifies the
    approved ledger record for (task, step, idempotency_key) -> the device
    command fires -> the task completes.

Also proves the hard rule: an APPROVED control of a safety-critical device
(locks/alarms/security/doors/gates) is still REFUSED by the connector — an
approval can never override it — and that an un-approved control never fires.
No component is stubbed over HTTP; only the local hub itself is the mock.
"""

from __future__ import annotations

import pytest
import requests

from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase6_connector_tools,
)
from tests.integration._server import Server, free_port


def _pair(connector_url: str, mock, hub_ref: str = "hub-sh") -> None:
    mock.complete_pair(hub_ref)
    start = requests.post(f"{connector_url}/link/start")
    assert start.status_code == 200, start.text
    done = requests.post(f"{connector_url}/link/complete", params={"hub_ref": hub_ref})
    assert done.status_code == 200, done.text


def _create_task(base: str, tool: str, args: dict, risk: str, request: str) -> str:
    r = requests.post(
        f"{base}/tasks",
        json={
            "request": request,
            "steps": [{"agent": "smart_home", "tool": tool, "risk_level": risk, "result": {"args": args}}],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    """Boot the smart-home connector + task-runner on free ports, sharing one
    approval ledger, with the mock local hub injected into the connector."""
    from integrations.mock_providers.smart_home_mock import MockSmartHomeTransport
    from integrations.smart_home import api as sh_api
    from integrations.smart_home.connector import SmartHomeConnector
    from services.task_runner.app import api as task_api
    from services.task_runner.app.manager import TaskManager

    monkeypatch.setenv("VIORUS_DATA_DIR", str(tmp_path))
    connector_port = free_port()
    task_runner_port = free_port()
    monkeypatch.setenv("VIORUS_SMART_HOME_URL", f"http://127.0.0.1:{connector_port}")

    mock = MockSmartHomeTransport()
    sh_api._vault = None
    sh_api._connector = None
    sh_api.get_connector = lambda: SmartHomeConnector(sh_api.get_vault(), transport=mock)

    task_api._manager = TaskManager(tmp_path / "task_runner.db")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    services = {
        "connector": Server(sh_api.app, connector_port, probe_path="/health"),
        "task_runner": Server(task_api.app, task_runner_port, probe_path="/tasks"),
    }
    for svc in services.values():
        svc.start()

    connector_url = f"http://127.0.0.1:{connector_port}"
    _pair(connector_url, mock)
    yield {
        "connector": connector_url,
        "task_runner": f"http://127.0.0.1:{task_runner_port}",
        "mock": mock,
    }


def test_observe_status_runs_without_approval(stack) -> None:
    base = stack["task_runner"]
    tid = _create_task(base, "smart_home.device_status", {"category": "light"}, "observe", "check the lights")
    assert requests.post(f"{base}/tasks/{tid}/start").status_code == 200

    r = requests.post(f"{base}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    step = r.json()["step"]
    assert step["ok"] is True
    assert r.json()["task"]["status"] == "completed"
    assert stack["mock"].controls == []  # a status read never controls anything


def test_control_full_loop_pauses_approve_fires(stack) -> None:
    base = stack["task_runner"]
    tid = _create_task(
        base,
        "smart_home.control",
        {
            "device_id": "light-living",
            "device_name": "Living Room Light",
            "category": "light",
            "action": "on",
            "value": "on",
        },
        "execute",
        "turn on the living room light",
    )
    assert requests.post(f"{base}/tasks/{tid}/start").status_code == 200

    # run -> pauses at the approval gate; nothing fired
    r = requests.post(f"{base}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "waiting_approval"
    assert stack["mock"].controls == []

    # the diff card shows the device + exact action + target value
    approvals = requests.get(f"{base}/tasks/{tid}/approvals").json()
    assert len(approvals) == 1
    card = approvals[0]["diff_card"]
    assert card.get("device_id") == "light-living"
    assert card.get("action") == "on"
    assert card.get("value") == "on"

    # approve -> the executor dispatches to the connector, which re-verifies
    # the ledger record and fires the command; task completes
    apr = requests.post(f"{base}/approvals/{approvals[0]['approval_id']}/approve")
    assert apr.status_code == 200, apr.text
    assert apr.json()["status"] == "completed", apr.json()

    # the command reached the hub exactly once
    assert stack["mock"].controls == [{"device_id": "light-living", "action": "on", "value": "on"}]


def test_control_never_fires_without_approval(stack) -> None:
    base = stack["task_runner"]
    tid = _create_task(
        base,
        "smart_home.control",
        {
            "device_id": "ac-bedroom",
            "device_name": "Bedroom AC",
            "category": "climate",
            "action": "on",
            "value": "on",
        },
        "execute",
        "turn on the bedroom ac",
    )
    assert requests.post(f"{base}/tasks/{tid}/start").status_code == 200
    r = requests.post(f"{base}/tasks/{tid}/run")
    assert r.json()["task"]["status"] == "waiting_approval"

    approval_id = requests.get(f"{base}/tasks/{tid}/approvals").json()[0]["approval_id"]
    rejected = requests.post(f"{base}/approvals/{approval_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "cancelled"
    assert stack["mock"].controls == []  # rejection means it never fires


def test_approved_control_of_lock_is_refused(stack) -> None:
    """The hard rule: even an approved control of a safety-critical device is
    refused by the connector — the connector is the last line, never a
    convenience shortcut."""
    base = stack["task_runner"]
    tid = _create_task(
        base,
        "smart_home.control",
        {
            "device_id": "lock-front",
            "device_name": "Front Door Lock",
            "category": "security",
            "action": "unlock",
            "value": "unlock",
        },
        "execute",
        "unlock the front door",
    )
    assert requests.post(f"{base}/tasks/{tid}/start").status_code == 200
    r = requests.post(f"{base}/tasks/{tid}/run")
    assert r.json()["task"]["status"] == "waiting_approval"

    approval_id = requests.get(f"{base}/tasks/{tid}/approvals").json()[0]["approval_id"]
    apr = requests.post(f"{base}/approvals/{approval_id}/approve")
    assert apr.status_code == 200, apr.text
    assert apr.json()["status"] == "failed"  # the connector refused, loudly
    assert stack["mock"].controls == []  # the lock was never touched
