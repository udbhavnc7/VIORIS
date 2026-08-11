"""API gateway: Phase 5.3 remote-control endpoints require a device JWT and an
active remote session. The computer-agent hop is stubbed so no local desktop
needs to be running."""

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture(autouse=True)
def _stub_computer(monkeypatch):
    async def fake_computer(method, path, body=None):
        if path.startswith("/remote/session/active"):
            return {"session": {"session_id": "rs_test", "device_id": "dev-1", "expired": False}}
        if path == "/remote/session/end":
            return {"device_id": "dev-1", "ended_sessions": 1}
        if path == "/remote/lock":
            return {"device_id": "dev-1", "ok": True, "ended_sessions": 1}
        if path == "/screenshot":
            return {"path": "last_shot.png"}
        if path == "/remote/input":
            return {"delivered": True}
        raise AssertionError(f"unexpected computer call {method} {path}")

    async def fake_computer_bytes(method, path):
        if path.startswith("/remote/screenshot-image"):
            return b"\x89PNG" + b"\x00" * 32
        raise AssertionError(f"unexpected binary computer call {method} {path}")

    monkeypatch.setattr(gw, "_computer_request", fake_computer)
    monkeypatch.setattr(gw, "_computer_request_bytes", fake_computer_bytes)
    return fake_computer


@pytest.fixture
def client():
    gw._store = PairingStore(secret="test-secret")
    gw._FRAME_LOCK.clear()
    with TestClient(gw.app) as c:
        yield c


def _pair_and_get_jwt(client) -> tuple[str, str]:
    pair = client.post("/auth/device/pair", json={"name": "phone"}).json()
    ex = client.post(
        "/auth/device/exchange",
        json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
    ).json()
    return pair["device_id"], ex["jwt"]


def test_remote_endpoints_require_device_jwt(client):
    for method, path in [
        ("post", "/v1/remote/start"),
        ("get", "/v1/remote/session"),
        ("get", "/v1/remote/screenshot"),
        ("post", "/v1/remote/input"),
        ("post", "/v1/remote/end"),
        ("post", "/v1/remote/lock"),
    ]:
        r = getattr(client, method)(path, headers={"Authorization": "Bearer junk"})
        assert r.status_code in (401, 403), path


def test_screenshot_and_input_require_active_session(client, _stub_computer):
    """End-to-end via gateway: start -> active session -> screenshot/input.
    Session gating is exercised: without an active session, both refuse."""
    device_id, jwt = _pair_and_get_jwt(client)

    no_active = {"session": None}

    async def fake_computer_no_session(method, path, body=None):
        if path.startswith("/remote/session/active"):
            return no_active
        raise AssertionError("must not reach agent without a session")

    gw._computer_request = fake_computer_no_session
    headers = {"Authorization": f"Bearer {jwt}"}
    assert client.get("/v1/remote/screenshot", headers=headers).status_code == 403
    assert (
        client.post(
            "/v1/remote/input",
            json={"session_id": "rs_x", "action": "type", "text": "hi"},
            headers=headers,
        ).status_code
        == 403
    )

    gw._computer_request = _stub_computer
    r = client.post(
        "/v1/remote/input",
        json={"session_id": "rs_test", "action": "type", "text": "hi"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["delivered"] is True


def test_screenshot_is_throttled_on_demand_only(client):
    _, jwt = _pair_and_get_jwt(client)
    headers = {"Authorization": f"Bearer {jwt}"}

    first = client.get("/v1/remote/screenshot", headers=headers)
    assert first.status_code == 200

    second = client.get("/v1/remote/screenshot", headers=headers)
    assert second.status_code == 429  # on-demand only, never a stream


def test_stop_kills_remote_sessions(client):
    _, jwt = _pair_and_get_jwt(client)
    r = client.post("/v1/stop", headers={"Authorization": f"Bearer {jwt}"})
    assert r.status_code == 200
    assert r.json()["remote_sessions_ended"] == 1