"""Gateway integration for the opt-in local voiceprint second factor."""

import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

from packages.shared.dialogue import CallSessionTrust
from packages.shared.permission_engine import PermissionEngine
from packages.shared.second_factor import VOICEPRINT_FACTOR, proof_factor, verify
from services.api_gateway.app import main as gw
from services.api_gateway.app.call_audio import float32_to_pcm16_bytes
from services.api_gateway.app.pairing import PairingStore


def _pcm_b64(energy: float = 0.5, seconds: float = 0.1) -> str:
    audio = np.ones(int(16000 * seconds), dtype=np.float32) * energy
    return base64.b64encode(float32_to_pcm16_bytes(audio)).decode("ascii")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIORIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIORIS_VOICEPRINT_FILE", str(tmp_path / "vp.npz"))
    monkeypatch.delenv("VIORIS_VOICEPRINT_ENABLED", raising=False)
    monkeypatch.delenv("VIORIS_VOICEPRINT_ENCODER", raising=False)
    PermissionEngine.reset()
    gw._store = PairingStore(secret="test-secret")
    gw._pipeline = None
    with TestClient(gw.app) as c:
        pair = c.post("/auth/device/pair", json={"name": "phone"}).json()
        exchange = c.post(
            "/auth/device/exchange",
            json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
        ).json()
        c.auth_headers = {"Authorization": f"Bearer {exchange['jwt']}"}
        yield c
    PermissionEngine.set_call_trust(None)
    PermissionEngine.set_voiceprint(None)
    PermissionEngine.reset()
    gw._pipeline = None
    gw._voiceprint = None
    gw._voiceprint_encoder_name = "none"


def _enable_voiceprint(client, monkeypatch):
    """Simulate a gateway started with voiceprint enabled (dev encoder)."""
    monkeypatch.setenv("VIORIS_VOICEPRINT_ENABLED", "1")
    monkeypatch.setenv("VIORIS_VOICEPRINT_ENCODER", "fake")
    monkeypatch.setattr(gw, "CRITICAL_COOLDOWN_SECONDS", 0)
    gw._configure_voiceprint()
    assert client.post(
        "/v1/voiceprint/enroll",
        headers=client.auth_headers,
        json={"segments": [_pcm_b64()]},
    ).status_code == 200

    captured: dict = {}

    async def fake_proxy(method, path, device, body=None, headers=None):
        if method == "GET" and path == "/approvals":
            return [
                {
                    "approval_id": "apr-1",
                    "tool": "system.register_payment",
                    "created_at": "2020-01-01T00:00:00Z",
                }
            ]
        if method == "POST" and path == "/approvals/apr-1/approve":
            captured["headers"] = headers
            return {"staged": True, "stage_id": "stage-1", "second_factor": VOICEPRINT_FACTOR}
        raise AssertionError(f"unexpected proxy call {method} {path}")

    monkeypatch.setattr(gw, "_proxy", fake_proxy)
    return captured


def test_voiceprint_is_disabled_by_default(client):
    status = client.get("/v1/voiceprint/status", headers=client.auth_headers)
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["enrolled"] is False
    assert status.json()["verified_call_session"] is False

    enroll = client.post(
        "/v1/voiceprint/enroll",
        headers=client.auth_headers,
        json={"segments": [_pcm_b64()]},
    )
    assert enroll.status_code == 403
    assert "disabled" in enroll.json()["detail"].lower()


def test_status_requires_device_auth(client):
    assert client.get("/v1/voiceprint/status").status_code == 401
    assert client.post("/v1/voiceprint/enroll", json={"segments": [_pcm_b64()]}).status_code == 401


def test_enrollment_makes_the_factor_usable(client, monkeypatch):
    _enable_voiceprint(client, monkeypatch)
    status = client.get("/v1/voiceprint/status", headers=client.auth_headers).json()
    assert status["enabled"] is True
    assert status["enrolled"] is True
    assert status["encoder"] == "fake"
    assert status["verified_call_session"] is False


def test_voiceprint_approval_requires_a_verified_call(client, monkeypatch):
    _enable_voiceprint(client, monkeypatch)
    r = client.post(
        "/v1/approvals/apr-1/approve",
        headers=client.auth_headers,
        json={
            "phrase": "confirm critical",
            "voiceprint_audio": _pcm_b64(),
        },
    )
    assert r.status_code == 403
    assert "voiceprint was verified at pickup" in r.json()["detail"]


def test_voiceprint_approval_sends_a_scoped_proof_to_the_runner(client, monkeypatch):
    captured = _enable_voiceprint(client, monkeypatch)
    from packages.shared.voiceprint import FakeEncoder, VoiceprintManager

    vp = VoiceprintManager(encoder=FakeEncoder(), store_path=gw._voiceprint.store_path)
    audio = np.ones(16000, dtype=np.float32) * 0.5
    trust = CallSessionTrust(voiceprint_checker=lambda a, sr: vp.verify(a, sr))
    trust.start_session()
    assert trust.verify_voiceprint(audio)[0] is True
    PermissionEngine.set_call_trust(trust)
    monkeypatch.setattr(gw, "load_or_create_key", lambda: b"integration-key")

    r = client.post(
        "/v1/approvals/apr-1/approve",
        headers=client.auth_headers,
        json={"phrase": "confirm critical", "voiceprint_audio": _pcm_b64()},
    )

    assert r.status_code == 200
    assert r.json()["staged"] is True
    assert r.json()["second_factor"] == VOICEPRINT_FACTOR
    proof = captured["headers"]["X-Vioris-Second-Factor"]
    assert proof_factor(proof) == VOICEPRINT_FACTOR
    assert verify(proof, "apr-1", b"integration-key")[0] is True


def test_pipeline_checks_the_enrolled_voice_at_call_pickup(client, monkeypatch):
    _enable_voiceprint(client, monkeypatch)
    pipeline = gw.get_pipeline()
    pipeline.dialogue.start_call()
    trust = pipeline.dialogue.trust
    assert trust._voiceprint_checker is not None
    assert trust.verify_voiceprint(np.ones(16000, dtype=np.float32) * 0.5)[0] is True


def test_bare_yes_is_not_a_valid_critical_phrase(client, monkeypatch):
    _enable_voiceprint(client, monkeypatch)
    r = client.post(
        "/v1/approvals/apr-1/approve",
        headers=client.auth_headers,
        json={"phrase": "yes", "voiceprint_audio": _pcm_b64()},
    )
    assert r.status_code == 403
    assert "confirm critical" in r.json()["detail"]
