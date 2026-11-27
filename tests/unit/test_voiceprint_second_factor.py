"""Voiceprint-backed Critical second factor (Phase 8.1 wired into approvals).

A voiceprint proof is only minted after a fresh sample matches the enrolled
speaker inside a voiceprint-verified call, is bound to one approval, expires,
and is re-verified by the task-runner before anything is staged.
"""

import base64
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from services.api.app.websocket import WSMessage
from services.api_gateway.app.call_audio import float32_to_pcm16_bytes

from packages.shared.dialogue import CallSessionTrust
from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase2_tools,
    register_phase3_files_tools,
)
from packages.shared.schemas import RiskTier, TaskStep
from packages.shared.second_factor import (
    PIN_FACTOR,
    VOICEPRINT_FACTOR,
    mint,
    proof_factor,
    verify,
    verify_with_factor,
)
from packages.shared.voiceprint import FakeEncoder, VoiceprintManager
from services.api_gateway.app import main as gw
from services.task_runner.app.api import app as tr_app
from services.task_runner.app.manager import TaskManager

KEY = b"voiceprint-second-factor-test-key"


class DirectionEncoder:
    """Test encoder with unambiguous directions, unlike the energy-based fake."""

    def embed(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        energy = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
        out = np.zeros(256, dtype=np.float32)
        out[0] = 1.0 if energy > 0.1 else -1.0
        return out


class TestFactorAwareProofs:
    def test_pin_proof_keeps_legacy_shape(self):
        proof = mint("apr-1", KEY, now=1000.0)
        assert proof.count(".") == 1
        assert verify(proof, "apr-1", KEY, now=1001.0)[0] is True
        assert proof_factor(proof) == PIN_FACTOR

    def test_voiceprint_proof_verifies_and_records_factor(self):
        proof = mint("apr-1", KEY, now=1000.0, factor=VOICEPRINT_FACTOR)
        assert ".voiceprint." in proof
        assert verify(proof, "apr-1", KEY, now=1001.0)[0] is True
        ok, _, factor = verify_with_factor(proof, "apr-1", KEY, now=1001.0)
        assert ok is True
        assert factor == VOICEPRINT_FACTOR
        assert proof_factor(proof) == VOICEPRINT_FACTOR

    def test_proof_is_bound_to_one_approval(self):
        proof = mint("apr-1", KEY, now=1000.0, factor=VOICEPRINT_FACTOR)
        assert verify(proof, "apr-2", KEY, now=1001.0)[0] is False

    def test_pin_proof_cannot_be_relabelled_as_voiceprint(self):
        exp, sig = mint("apr-1", KEY, now=1000.0).split(".", 1)
        assert verify(f"{exp}.voiceprint.{sig}", "apr-1", KEY, now=1001.0)[0] is False

    def test_expired_voiceprint_proof_is_rejected(self):
        proof = mint("apr-1", KEY, now=1000.0, factor=VOICEPRINT_FACTOR)
        assert verify(proof, "apr-1", KEY, now=2000.0)[0] is False

    def test_factor_policy_can_exclude_voiceprint(self):
        proof = mint("apr-1", KEY, now=1000.0, factor=VOICEPRINT_FACTOR)
        assert verify(
            proof, "apr-1", KEY, now=1001.0, allowed_factors=(PIN_FACTOR,)
        )[0] is False

    def test_missing_and_malformed_proofs_fail_closed(self):
        assert verify(None, "apr-1", KEY)[0] is False
        assert verify("not-a-proof", "apr-1", KEY)[0] is False
        assert verify("bad.voiceprint.sig", "apr-1", KEY)[0] is False


class TestPermissionEngineHook:
    def test_enrolled_voice_passes(self, tmp_path):
        vp = VoiceprintManager(encoder=FakeEncoder(), store_path=tmp_path / "vp.npz")
        audio = np.random.RandomState(1).randn(16000).astype(np.float32)
        vp.enroll_single(audio)
        PermissionEngine.reset()
        PermissionEngine.set_voiceprint(vp)
        try:
            ok, note = PermissionEngine.verify_critical_second_factor(audio=audio)
            assert ok is True
            assert "verified" in note
        finally:
            PermissionEngine.set_voiceprint(None)
            PermissionEngine.reset()

    def test_no_enrollment_fails_closed(self, tmp_path):
        vp = VoiceprintManager(
            encoder=FakeEncoder(), store_path=tmp_path / "missing.npz"
        )
        PermissionEngine.reset()
        PermissionEngine.set_voiceprint(vp)
        try:
            ok, note = PermissionEngine.verify_critical_second_factor(
                audio=np.zeros(16000, dtype=np.float32)
            )
            assert ok is False
            assert "not enrolled" in note
        finally:
            PermissionEngine.set_voiceprint(None)
            PermissionEngine.reset()


@pytest.fixture
def verified_call(monkeypatch, tmp_path):
    """A call whose voiceprint was verified at pickup, plus its matching audio."""
    vp = VoiceprintManager(encoder=DirectionEncoder(), store_path=tmp_path / "vp.npz")
    audio = np.ones(16000, dtype=np.float32) * 0.5
    vp.enroll_single(audio)
    trust = CallSessionTrust(voiceprint_checker=lambda a, sr: vp.verify(a, sr))
    trust.start_session()
    assert trust.verify_voiceprint(audio)[0] is True

    PermissionEngine.reset()
    PermissionEngine.set_voiceprint(vp)
    PermissionEngine.set_call_trust(trust)
    monkeypatch.setattr(gw, "load_or_create_key", lambda: KEY)
    yield vp, trust, audio
    PermissionEngine.set_call_trust(None)
    PermissionEngine.set_voiceprint(None)
    PermissionEngine.reset()


class TestGatewayProofMinting:
    def test_distinct_phrase_is_required(self):
        assert gw._is_critical_confirm("confirm critical") is True
        assert gw._is_critical_confirm("yes") is False
        assert gw._is_critical_confirm("approve") is False

    def test_mint_requires_an_active_verified_call(self, verified_call):
        _, _, audio = verified_call
        PermissionEngine.set_call_trust(None)
        proof, error = gw._mint_voiceprint_proof("apr-1", audio)
        assert proof is None
        assert "voiceprint was verified at pickup" in error

    def test_mint_requires_a_fresh_matching_sample(self, verified_call):
        proof, error = gw._mint_voiceprint_proof(
            "apr-1", np.zeros(16000, dtype=np.float32)
        )
        assert proof is None
        assert "rejected" in error

    def test_mint_produces_an_approval_scoped_verifiable_proof(self, verified_call):
        _, _, audio = verified_call
        proof, error = gw._mint_voiceprint_proof("apr-1", audio)
        assert proof is not None
        assert error is None
        ok, _, factor = verify_with_factor(proof, "apr-1", KEY)
        assert ok is True
        assert factor == VOICEPRINT_FACTOR
        assert verify(proof, "apr-2", KEY)[0] is False


@pytest.fixture
def tr_client(tmp_path, monkeypatch):
    import services.task_runner.app.api as api

    PermissionEngine.reset()
    register_phase2_tools()
    register_phase3_files_tools()
    mgr = TaskManager(tmp_path / "vp_runner.db")
    monkeypatch.setattr(api, "get_manager", lambda: mgr)
    monkeypatch.setattr(api, "CRITICAL_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(api, "load_or_create_key", lambda: KEY)
    monkeypatch.setattr(
        api,
        "dispatch_step",
        lambda task_id: {"queued": False, "task": mgr.get(task_id)},
    )
    with TestClient(tr_app) as c:
        yield c, mgr
    for stage in mgr.list_stages():
        if stage["status"] == "staged":
            try:
                mgr.cancel_staged(stage["stage_id"])
            except (KeyError, ValueError):
                pass
    mgr.close()
    PermissionEngine.reset()


def _waiting_payment(mgr):
    step = TaskStep(
        agent="system",
        tool="system.register_payment",
        risk_level=RiskTier.CRITICAL,
        result={"args": {"payee": "Landlord", "amount": 1200, "currency": "INR"}},
    )
    task = mgr.create_task("pay landlord", [step])
    mgr.start(task.task_id)
    task, _ = mgr.run_next_step(task.task_id, lambda s, t: {"ok": True})
    return task, mgr.pending_approvals(task.task_id)[0]


class TestTaskRunnerAcceptsVoiceprint:
    def test_voiceprint_proof_stages_and_is_audited(self, tr_client):
        client, mgr = tr_client
        _, approval = _waiting_payment(mgr)
        proof = mint(approval["approval_id"], KEY, factor=VOICEPRINT_FACTOR)

        r = client.post(
            f"/approvals/{approval['approval_id']}/approve",
            headers={"X-Vioris-Second-Factor": proof},
        )

        assert r.status_code == 200
        assert r.json()["staged"] is True
        assert r.json()["second_factor"] == VOICEPRINT_FACTOR
        events = mgr.audit_events_copy()
        approved = next(e for e in events if e["action"] == "approved")
        staged = next(e for e in events if e["action"] == "staged")
        assert approved["detail"]["second_factor"] == VOICEPRINT_FACTOR
        assert staged["detail"]["second_factor"] == VOICEPRINT_FACTOR

    def test_pin_proof_is_still_accepted(self, tr_client):
        client, mgr = tr_client
        _, approval = _waiting_payment(mgr)
        proof = mint(approval["approval_id"], KEY, factor=PIN_FACTOR)

        r = client.post(
            f"/approvals/{approval['approval_id']}/approve",
            headers={"X-Vioris-Second-Factor": proof},
        )

        assert r.status_code == 200
        assert r.json()["second_factor"] == PIN_FACTOR

    def test_tampered_voiceprint_proof_is_refused(self, tr_client):
        client, mgr = tr_client
        _, approval = _waiting_payment(mgr)
        proof = mint("some-other-approval", KEY, factor=VOICEPRINT_FACTOR)

        r = client.post(
            f"/approvals/{approval['approval_id']}/approve",
            headers={"X-Vioris-Second-Factor": proof},
        )

        assert r.status_code == 403
        assert mgr.get(approval["task_id"]).status.value == "waiting_approval"


class _WSPipeline:
    """Minimal pipeline surface used by the audio_end handler."""

    def __init__(self, trust, session):
        self.dialogue = SimpleNamespace(trust=trust)
        self._session = session
        self.handled: list[tuple[str, str]] = []

    def get_session(self, call_id):
        return self._session

    async def handle_phone_message(self, call_id, text):
        self.handled.append((call_id, text))
        return {"response_text": "ok", "action": None}


def _audio_hub(pipeline, sent):
    from services.api.app.websocket import WebSocketHub

    hub = WebSocketHub()

    async def record(data):
        sent.append(data)

    hub.send_to_phones = record
    gw._setup_hub_handlers(hub, pipeline)
    return hub


async def _send_audio(hub, call_id, audio, text, monkeypatch):
    monkeypatch.setattr(
        "packages.shared.stt.WhisperSTT.transcribe",
        lambda self, a, *args, **kwargs: text,
    )
    b64 = base64.b64encode(float32_to_pcm16_bytes(audio)).decode()
    await hub._handlers["call_audio"][0](
        WSMessage(type="call_audio", payload={"call_id": call_id, "data": b64})
    )
    await hub._handlers["audio_end"][0](
        WSMessage(type="audio_end", payload={"call_id": call_id})
    )


class TestWsAudioEnd:
    @pytest.mark.asyncio
    async def test_pickup_voice_check_runs_before_any_utterance(self, verified_call, monkeypatch):
        vp, trust, audio = verified_call
        trust.end_session()
        trust.start_session()  # fresh, unverified pickup
        assert trust.session.voiceprint_verified is False
        monkeypatch.setattr(gw, "_voiceprint", vp)
        pipeline = _WSPipeline(trust, SimpleNamespace(metadata={}))
        sent: list[dict] = []
        hub = _audio_hub(pipeline, sent)

        await _send_audio(hub, "call-1", audio, "pay my landlord", monkeypatch)

        assert trust.session.voiceprint_verified is True
        assert pipeline.handled == []  # first sample verifies; it executes nothing
        assert any("Voice verified" in str(s) for s in sent)

    @pytest.mark.asyncio
    async def test_critical_confirm_mints_and_forwards_voiceprint_proof(
        self, verified_call, monkeypatch
    ):
        vp, trust, audio = verified_call  # session already voiceprint-verified at pickup
        monkeypatch.setattr(gw, "_voiceprint", vp)
        monkeypatch.setattr(gw, "load_or_create_key", lambda: KEY)
        register_phase2_tools()
        session = SimpleNamespace(
            metadata={
                "pending_call_approval": {"task_id": "t-1", "approval_id": "apr-1"}
            }
        )
        pipeline = _WSPipeline(trust, session)
        sent: list[dict] = []
        calls: list[tuple] = []

        async def fake_request(method, path, body=None, headers=None):
            calls.append((method, path, headers))
            if method == "GET":
                return [
                    {
                        "approval_id": "apr-1",
                        "tool": "system.register_payment",
                        "created_at": "2020-01-01T00:00:00Z",
                    }
                ]
            return {"task_id": "t-1", "status": "staged", "staged": True}

        monkeypatch.setattr(gw, "_local_task_request", fake_request)
        hub = _audio_hub(pipeline, sent)

        await _send_audio(hub, "call-1", audio, "confirm critical", monkeypatch)

        posts = [c for c in calls if c[0] == "POST"]
        assert len(posts) == 1
        proof = posts[0][2]["X-Vioris-Second-Factor"]
        assert verify(proof, "apr-1", KEY)[0] is True
        assert proof_factor(proof) == VOICEPRINT_FACTOR
        assert pipeline.handled == []  # consumed as an approval, not a new utterance
        assert "pending_call_approval" not in session.metadata
        assert any("approval_result" in str(s) for s in sent)

    @pytest.mark.asyncio
    async def test_bare_yes_does_not_approve_a_critical_action(
        self, verified_call, monkeypatch
    ):
        vp, trust, audio = verified_call
        monkeypatch.setattr(gw, "_voiceprint", vp)
        register_phase2_tools()
        session = SimpleNamespace(
            metadata={
                "pending_call_approval": {"task_id": "t-1", "approval_id": "apr-1"}
            }
        )
        pipeline = _WSPipeline(trust, session)
        sent: list[dict] = []
        calls: list[tuple] = []

        async def fake_request(method, path, body=None, headers=None):
            calls.append((method, path, headers))
            return []

        monkeypatch.setattr(gw, "_local_task_request", fake_request)
        hub = _audio_hub(pipeline, sent)

        await _send_audio(hub, "call-1", audio, "yes", monkeypatch)

        assert not [c for c in calls if c[0] == "POST"]  # no approval was sent
        assert pipeline.handled == [("call-1", "yes")]  # fell through as an utterance
