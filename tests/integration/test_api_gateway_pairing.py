"""API gateway integration: full pairing flow over HTTP + WebSocket gating."""

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture
def client():
    gw._store = PairingStore(secret="test-secret")
    with TestClient(gw.app) as c:
        yield c


def test_full_pair_flow(client):
    pair = client.post("/auth/device/pair", json={"name": "my pixel"})
    assert pair.status_code == 200
    pid = pair.json()["device_id"]

    exchange = client.post(
        "/auth/device/exchange",
        json={"device_id": pid, "token": pair.json()["pairing_token"]},
    )
    assert exchange.status_code == 200
    assert exchange.json()["token_type"] == "bearer"
    assert exchange.json()["jwt"]

    listed = client.get("/devices").json()
    assert len(listed) == 1 and listed[0]["device_id"] == pid

    revoke = client.post(f"/devices/{pid}/revoke")
    assert revoke.status_code == 200 and revoke.json()["revoked"] is True


def test_exchange_rejects_wrong_device(client):
    pair = client.post("/auth/device/pair", json={"name": "phone"}).json()
    wrong = client.post(
        "/auth/device/exchange",
        json={"device_id": "dev_not_yours", "token": pair["pairing_token"]},
    )
    assert wrong.status_code == 401


def test_ws_rejects_bad_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/device?token=nonsense") as ws:
            ws.receive_text()


def test_ws_accepts_valid_device(client):
    pair = client.post("/auth/device/pair", json={"name": "phone"}).json()
    ex = client.post(
        "/auth/device/exchange",
        json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
    ).json()

    with client.websocket_connect(f"/ws/device?token={ex['jwt']}") as ws:
        msg = ws.receive_json()
        assert msg["device_id"] == pair["device_id"]
        assert msg["status"] == "connected"