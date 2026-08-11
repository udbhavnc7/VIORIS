"""API gateway: device-authenticated proxy endpoints for the mobile app (Prompt 5.2).

The mobile app only talks to the gateway; the gateway proxies tasks/approvals/
stop to the local task-runner. These tests verify the JWT gate (with a stubbed
proxy) so no backend service is required.
"""

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture(autouse=True)
def _stub_proxy(monkeypatch):
    async def fake_proxy(method, path, device, body=None):
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
    assert approvals.json()["approvals"]["method"] == "GET"
    assert approvals.json()["approvals"]["device"] is not None


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