"""API gateway integration: device-authenticated call trigger CRUD."""

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIORIS_DATA_DIR", str(tmp_path))
    gw._store = PairingStore(secret="test-secret")
    gw._pipeline = None
    with TestClient(gw.app) as test_client:
        pair = test_client.post("/auth/device/pair", json={"name": "phone"}).json()
        exchange = test_client.post(
            "/auth/device/exchange",
            json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
        ).json()
        test_client.auth_headers = {"Authorization": f"Bearer {exchange['jwt']}"}
        yield test_client
    gw._pipeline = None


def test_call_triggers_require_device_auth(client):
    assert client.get("/v1/call/triggers").status_code == 401
    assert (
        client.post("/v1/call/triggers", json={"kind": "time", "at": "19:00"}).status_code
        == 401
    )
    assert client.delete("/v1/call/triggers/nope").status_code == 401


def test_create_list_and_delete_time_trigger(client):
    created = client.post(
        "/v1/call/triggers",
        headers=client.auth_headers,
        json={"kind": "time", "at": "19:00", "label": "Evening check-in"},
    )
    assert created.status_code == 200
    trigger = created.json()["trigger"]
    assert trigger["kind"] == "time"
    assert trigger["at"] == "19:00"
    assert trigger["label"] == "Evening check-in"

    listed = client.get("/v1/call/triggers", headers=client.auth_headers).json()["triggers"]
    assert [t["trigger_id"] for t in listed] == [trigger["trigger_id"]]

    deleted = client.delete(
        f"/v1/call/triggers/{trigger['trigger_id']}", headers=client.auth_headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/v1/call/triggers", headers=client.auth_headers).json()["triggers"] == []

    missing = client.delete(
        f"/v1/call/triggers/{trigger['trigger_id']}", headers=client.auth_headers
    )
    assert missing.status_code == 404


def test_create_task_completion_trigger(client):
    created = client.post(
        "/v1/call/triggers",
        headers=client.auth_headers,
        json={"kind": "task_completed", "match": "deploy", "one_shot": True},
    )
    assert created.status_code == 200
    trigger = created.json()["trigger"]
    assert trigger["kind"] == "task_completed"
    assert trigger["match"] == "deploy"
    assert trigger["one_shot"] is True
    assert trigger["at"] == ""


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "whenever"},
        {"kind": "time"},
        {"kind": "time", "at": "7pm"},
        {"kind": "task_completed"},
    ],
)
def test_invalid_triggers_are_rejected(client, body):
    response = client.post(
        "/v1/call/triggers", headers=client.auth_headers, json=body
    )
    assert response.status_code == 422
