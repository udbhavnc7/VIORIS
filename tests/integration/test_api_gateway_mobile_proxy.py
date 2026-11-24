"""API gateway: device-authenticated proxy endpoints for the mobile app (Prompt 5.2).

The mobile app only talks to the gateway; the gateway proxies tasks/approvals/
stop to the local task-runner. These tests verify the JWT gate (with a stubbed
proxy) so no backend service is required.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture(autouse=True)
def _stub_proxy(monkeypatch):
    async def fake_proxy(method, path, device, body=None, headers=None):
        if method == "GET" and path == "/approvals":
            return [
                {
                    "approval_id": "apr_1",
                    "task_id": "task_1",
                    "step_id": "step_1",
                    "tool": "system.send_message",
                    "diff_card": {"recipient": "alice"},
                    "status": "pending",
                    "created_at": "2020-01-01 00:00:00",
                }
            ]
        return {"method": method, "path": path, "device": device.device_id, "body": body}

    async def fake_computer(method, path, body=None):
        return {"ended_sessions": 1}

    monkeypatch.setattr(gw, "_proxy", fake_proxy)
    monkeypatch.setattr(gw, "_computer_request", fake_computer)
    return fake_proxy


@pytest.fixture
def client():
    gw._store = PairingStore(secret="test-secret")
    with TestClient(gw.app) as c:
        yield c


def _pair_and_get_jwt(client) -> tuple[str, str]:
    pair = client.post("/auth/device/pair", json={"name": "phone"}).json()
    ex = client.post(
        "/auth/device/exchange",
        json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
    ).json()
    return pair["device_id"], ex["jwt"]


def test_proxy_endpoints_require_device_jwt(client):
    checks = [
        ("get", "/v1/tasks"),
        ("get", "/v1/approvals"),
        ("post", "/v1/stop"),
        ("get", "/v1/activity"),
    ]
    for method, path in checks:
        r = getattr(client, method)(path, headers={"Authorization": "Bearer junk"})
        assert r.status_code in (401, 403), path


def test_tasks_and_approvals_proxy_with_jwt(client, _stub_proxy):
    _, jwt = _pair_and_get_jwt(client)
    headers = {"Authorization": f"Bearer {jwt}"}

    tasks = client.get("/v1/tasks", headers=headers)
    assert tasks.status_code == 200
    assert tasks.json()["tasks"]["path"] == "/tasks"

    approvals = client.get("/v1/approvals", headers=headers)
    listed = approvals.json()["approvals"]
    assert isinstance(listed, list) and listed[0]["tool"] == "system.send_message"
    # Server-side consent bindings are never exposed to the client.
    assert "idempotency_key" not in listed[0]
    assert "action_hash" not in listed[0]


def test_approve_reject_and_stop_proxy_with_jwt(client, _stub_proxy):
    _, jwt = _pair_and_get_jwt(client)
    headers = {"Authorization": f"Bearer {jwt}"}

    r = client.post("/v1/approvals/apr_1/approve", headers=headers)
    assert r.status_code == 200
    assert r.json()["result"]["path"] == "/approvals/apr_1/approve"

    r = client.post("/v1/approvals/apr_1/reject", headers=headers)
    assert r.json()["result"]["path"] == "/approvals/apr_1/reject"

    r = client.post("/v1/stop", headers=headers)
    assert r.json()["result"]["path"] == "/stop-everything"


def test_explain_proxy_routes_with_jwt(client, _stub_proxy):
    _, jwt = _pair_and_get_jwt(client)
    headers = {"Authorization": f"Bearer {jwt}"}

    r = client.get("/v1/tasks/task_1/explain", headers=headers)
    assert r.status_code == 200
    assert r.json()["explanation"]["path"] == "/tasks/task_1/explain"

    r = client.get("/v1/tasks/task_1/steps/step_1/explain", headers=headers)
    assert r.status_code == 200
    assert r.json()["explanation"]["path"] == "/tasks/task_1/steps/step_1/explain"

    r = client.post("/v1/explain", headers=headers, json={"query": "why did you do that?"})
    assert r.status_code == 200
    assert r.json()["path"] == "/explain"
    assert r.json()["body"] == {"query": "why did you do that?"}

    r = client.get("/v1/explain/recent?limit=5", headers=headers)
    assert r.status_code == 200
    assert r.json()["path"] == "/explain/recent?limit=5"


def test_explain_routes_require_device_jwt(client):
    checks = [
        ("get", "/v1/tasks/task_1/explain"),
        ("get", "/v1/tasks/task_1/steps/step_1/explain"),
        ("get", "/v1/explain/recent"),
    ]
    for method, path in checks:
        r = getattr(client, method)(path, headers={"Authorization": "Bearer junk"})
        assert r.status_code in (401, 403), path
    r = client.post("/v1/explain", json={"query": "why"}, headers={"Authorization": "Bearer junk"})
    assert r.status_code in (401, 403)


def test_call_sessions_expose_iso_timestamps(client, monkeypatch):
    """Call sessions store epoch seconds; the API must hand the UI ISO strings."""
    session = SimpleNamespace(
        call_id="7c5a8694",
        status=SimpleNamespace(value="ringing"),
        source=SimpleNamespace(value="schedule"),
        turns=[],
        created_at=1_700_000_000.0,
    )
    monkeypatch.setattr(
        gw, "get_pipeline", lambda: SimpleNamespace(get_all_sessions=lambda: [session])
    )
    _, jwt = _pair_and_get_jwt(client)

    r = client.get("/v1/calls", headers={"Authorization": f"Bearer {jwt}"})
    assert r.status_code == 200
    call = r.json()["calls"][0]
    assert call["call_id"] == "7c5a8694"
    assert call["created_at"] == "2023-11-14T22:13:20+00:00"
    assert isinstance(call["created_at"], str)