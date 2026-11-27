"""Gateway status must report honest interaction mode (Phase 10.1)."""

import pytest
from fastapi.testclient import TestClient

import services.api_gateway.app.main as gw
from packages.shared.graceful_degradation import InteractionMode, SubsystemStatus
from services.api_gateway.app.pairing import PairingStore


@pytest.fixture
def client(monkeypatch):
    async def fake_probe(url, timeout=1.2):
        return True, "reachable"

    monkeypatch.setattr(gw, "_probe", fake_probe)
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


def test_status_exposes_degradation_summary(client, monkeypatch):
    def unavailable_stt(self, *args, **kwargs):
        self.stt.status = SubsystemStatus.UNAVAILABLE
        return InteractionMode.TEXT_ONLY

    monkeypatch.setattr(gw.DegradationMode, "check_all", unavailable_stt)

    r = client.get(
        "/v1/system/status", headers={"Authorization": f"Bearer {_jwt(client)}"}
    )

    assert r.status_code == 200
    body = r.json()
    assert body["interaction_mode"] == "text_only"
    assert body["text_only"] is True
    assert body["degradation"]["stt"]["status"] == "unavailable"
    assert "llm" in body["degradation"]


def test_status_defaults_to_voice_when_healthy(client):
    body = client.get(
        "/v1/system/status", headers={"Authorization": f"Bearer {_jwt(client)}"}
    ).json()

    assert body["text_only"] is False
    assert body["degradation"]["mode"] in ("voice", "voice_degraded")
    assert body["degradation"]["stt"]["status"] == "healthy"
