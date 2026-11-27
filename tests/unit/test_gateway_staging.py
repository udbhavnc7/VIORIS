"""Gateway must not advance a task whose Critical approval was staged."""

import pytest
from fastapi.testclient import TestClient

import services.api_gateway.app.main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gw, "_store", PairingStore(secret="test-secret"))
    with TestClient(gw.app) as c:
        yield c


def _jwt(client) -> str:
    pair = client.post("/auth/device/pair", json={"name": "phone"}).json()
    exchanged = client.post(
        "/auth/device/exchange",
        json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
    ).json()
    return exchanged["jwt"]


def test_staged_approval_does_not_call_advance_task(client, monkeypatch):
    approval = {
        "approval_id": "apr_1",
        "task_id": "task_1",
        "step_id": "step_1",
        "tool": "system.register_payment",
        "created_at": "2026-01-01T00:00:00Z",
    }
    advanced: list[str] = []

    async def fake_proxy(method, path, device, body=None, headers=None):
        if method == "GET" and path == "/approvals":
            return [approval]
        if path == "/approvals/apr_1/approve":
            return {
                "approved": "apr_1",
                "task_id": "task_1",
                "status": "running",
                "staged": True,
                "stage_id": "stage_1",
                "execute_after_seconds": 30,
                "undo_window_seconds": 300,
            }
        return {}

    async def fake_advance(device, task_id, max_steps=16):
        advanced.append(task_id)
        return {"task": {}}

    monkeypatch.setattr(gw, "_proxy", fake_proxy)
    monkeypatch.setattr(gw, "_advance_task", fake_advance)
    monkeypatch.setattr(gw, "CRITICAL_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(gw, "pin_matches", lambda pin: (True, "ok"))
    monkeypatch.setattr(gw, "mint", lambda *args, **kwargs: "proof")

    r = client.post(
        "/v1/approvals/apr_1/approve",
        json={"pin": "1234"},
        headers={"Authorization": f"Bearer {_jwt(client)}"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["staged"] is True
    assert body["stage_id"] == "stage_1"
    assert advanced == [], "advancing would fire the step before the cancel window"
