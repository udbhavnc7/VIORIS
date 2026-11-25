"""API gateway integration: ending an in-app call from the phone."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture
def client():
    gw._store = PairingStore(secret="test-secret")
    with TestClient(gw.app) as test_client:
        pair = test_client.post("/auth/device/pair", json={"name": "phone"}).json()
        exchange = test_client.post(
            "/auth/device/exchange",
            json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
        ).json()
        test_client.auth_headers = {"Authorization": f"Bearer {exchange['jwt']}"}
        yield test_client


def test_end_call_requires_device_auth(client):
    assert client.post("/v1/call/c1/end").status_code == 401


def test_end_unknown_call_reports_not_found(client):
    response = client.post("/v1/call/missing/end", headers=client.auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "not_found"


def test_end_call_uses_pipeline(client, monkeypatch):
    ender = AsyncMock(return_value=True)
    monkeypatch.setattr(
        gw, "get_pipeline", lambda: SimpleNamespace(handle_call_end=ender)
    )
    response = client.post("/v1/call/c1/end", headers=client.auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    ender.assert_awaited_once_with("c1")
