"""Kill-phrase runtime wiring (Phase 8.2).

The standalone listener existed before; these tests prove the same phrase is
enforced at every entry point: the shared matcher, the task manager, the
task-runner API, the gateway, and the voice loop before the planner runs.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from apps.desktop_agent.app.config import AgentConfig
from apps.desktop_agent.app.services.audio_source import ArraySourceInput
from apps.desktop_agent.app.services.state_machine import AgentStateMachine
from apps.desktop_agent.app.services.voice_loop import VoiceLoop
from apps.desktop_agent.app.services.wake_detector import ClapDetector
from packages.shared.kill_phrase import (
    DEFAULT_KILL_PHRASES,
    KillPhraseListener,
    detect_kill_phrase,
)
from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase1_tools,
    register_phase2_tools,
)
from packages.shared.schemas import AgentState, RiskTier, TaskStep
from services.api_gateway.app import main as gw
from services.api_gateway.app.pairing import PairingStore
from services.task_runner.app.api import app as tr_app
from services.task_runner.app.manager import TaskManager


# ─── shared matcher ──────────────────────────────────────────────────────────


class TestSharedDetector:
    def test_defaults_match_case_insensitively(self):
        assert detect_kill_phrase("Stop Everything!") is True
        assert detect_kill_phrase("VIORIS STOP") is True
        assert detect_kill_phrase("please emergency stop now") is True

    @pytest.mark.parametrize("phrase", DEFAULT_KILL_PHRASES)
    def test_every_default_phrase_matches(self, phrase):
        assert detect_kill_phrase(phrase) is True

    def test_ordinary_request_does_not_match(self):
        assert detect_kill_phrase("check unread mail") is False
        assert detect_kill_phrase("") is False
        assert detect_kill_phrase("   ") is False

    def test_custom_phrases_replace_defaults(self):
        assert detect_kill_phrase("halt", phrases=["nuke it"]) is False
        assert detect_kill_phrase("nuke it now", phrases=["nuke it"]) is True


# ─── task manager ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _registry():
    PermissionEngine.reset()
    register_phase1_tools()
    register_phase2_tools()
    yield
    PermissionEngine.reset()


def _manager_with_waiting_task(tmp_path) -> tuple[TaskManager, str]:
    mgr = TaskManager(Path(tmp_path) / "kill.db")
    step = TaskStep(
        agent="computer",
        tool="system.send_message",
        risk_level=RiskTier.EXECUTE,
        result={"args": {"recipient": "alice", "content": "hi", "channel": "sms"}},
    )
    task = mgr.create_task("send alice a message", [step])
    mgr.run_next_step(task.task_id, lambda s, task=None: {"ok": True})
    return mgr, task.task_id


class TestManagerKillPhrase:
    def test_kill_phrase_stops_tasks_and_purges_approvals(self, tmp_path):
        mgr, task_id = _manager_with_waiting_task(tmp_path)
        assert mgr.get(task_id).status.value == "waiting_approval"

        affected = mgr.kill_phrase("stop everything")

        assert affected == [task_id]
        assert mgr.get(task_id).status.value == "stopped"
        assert mgr.pending_approvals(task_id) == []

    def test_kill_phrase_is_audited_even_with_no_tasks(self, tmp_path):
        mgr = TaskManager(Path(tmp_path) / "empty.db")

        assert mgr.kill_phrase("emergency stop") == []

        events = mgr.audit_events_copy()
        kill_events = [
            e
            for e in events
            if e["action"] == "stopped"
            and e["detail"].get("trigger") == "kill_phrase"
        ]
        assert len(kill_events) == 1
        assert kill_events[0]["detail"]["affected"] == []


# ─── task-runner API ─────────────────────────────────────────────────────────


@pytest.fixture
def tr_client(tmp_path):
    import services.task_runner.app.api as tr_api

    tr_api._manager = TaskManager(Path(tmp_path) / "tr.db")
    with TestClient(tr_app) as c:
        yield c


class TestTaskRunnerKillPhrase:
    def test_kill_phrase_endpoint_stops_everything(self, tr_client, tmp_path):
        import services.task_runner.app.api as tr_api

        mgr, task_id = _manager_with_waiting_task(tmp_path)
        tr_api._manager = mgr

        r = tr_client.post("/kill-phrase", json={"transcript": "abort all"})

        assert r.status_code == 200
        body = r.json()
        assert body["killed"] is True
        assert body["stopped"] == [task_id]
        assert mgr.get(task_id).status.value == "stopped"

    def test_create_task_preempts_planning_on_kill_phrase(self, tr_client, tmp_path):
        import services.task_runner.app.api as tr_api

        mgr, task_id = _manager_with_waiting_task(tmp_path)
        tr_api._manager = mgr
        before = len(mgr.list_tasks(limit=1000))

        r = tr_client.post("/tasks", json={"request": "VIORIS STOP"})

        assert r.status_code == 200
        body = r.json()
        assert body["killed"] is True
        assert body["task"] is None
        assert body["task_id"] is None
        assert body["stopped"] == [task_id]
        # No new task was planned or persisted.
        assert len(mgr.list_tasks(limit=1000)) == before
        assert mgr.get(task_id).status.value == "stopped"

    def test_ordinary_request_still_creates_a_task(self, tr_client):
        r = tr_client.post("/tasks", json={"request": "check unread mail"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("killed") is not True
        assert body.get("task_id")


# ─── gateway ─────────────────────────────────────────────────────────────────


@pytest.fixture
def gw_client(monkeypatch):
    calls: list[tuple] = []

    async def fake_proxy(method, path, device, body=None, headers=None):
        calls.append((method, path, body))
        if path == "/kill-phrase":
            return {"killed": True, "stopped": ["task_1"], "count": 1}
        return {"task_id": "task_9", "task": {"task_id": "task_9"}, "blocked_tools": []}

    async def fake_computer(method, path, body=None):
        return {"ended_sessions": 0}

    monkeypatch.setattr(gw, "_proxy", fake_proxy)
    monkeypatch.setattr(gw, "_computer_request", fake_computer)
    gw._store = PairingStore(secret="test-secret")
    with TestClient(gw.app) as c:
        yield c, calls


def _jwt(client) -> str:
    pair = client.post("/auth/device/pair", json={"name": "phone"}).json()
    ex = client.post(
        "/auth/device/exchange",
        json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
    ).json()
    return ex["jwt"]


class TestGatewayKillPhrase:
    def test_create_task_kill_phrase_preempts_before_task_runner_plan(self, gw_client):
        client, calls = gw_client
        headers = {"Authorization": f"Bearer {_jwt(client)}"}

        r = client.post("/v1/tasks", json={"request": "stop everything"}, headers=headers)

        assert r.status_code == 200
        body = r.json()
        assert body["killed"] is True
        assert body["task"] is None
        assert body["stopped"] == ["task_1"]
        # Only the kill endpoint was called; the planner/task path was skipped.
        assert [c[1] for c in calls] == ["/kill-phrase"]

    def test_kill_phrase_endpoint_reports_match_and_stop(self, gw_client):
        client, calls = gw_client
        headers = {"Authorization": f"Bearer {_jwt(client)}"}

        r = client.post(
            "/v1/kill-phrase", json={"transcript": "emergency stop"}, headers=headers
        )

        assert r.status_code == 200
        body = r.json()
        assert body["matched"] is True
        assert body["killed"] is True
        assert body["stopped"] == ["task_1"]
        assert calls[-1][1] == "/kill-phrase"

    def test_kill_phrase_requires_device_auth(self, gw_client):
        client, _ = gw_client
        r = client.post("/v1/kill-phrase", json={"transcript": "stop everything"})
        assert r.status_code in (401, 403)


# ─── voice loop ──────────────────────────────────────────────────────────────


class FakeWake:
    frame_size = 1280
    threshold = 0.5

    def __init__(self, activate_after=1):
        self._count = 0
        self._activate_after = activate_after

    def load(self):
        return None

    def is_activated(self, frame):
        self._count += 1
        return self._count > self._activate_after


class FakeSTT:
    def __init__(self, transcript):
        self.transcript = transcript
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.transcript


class FakeTTS:
    def __init__(self):
        self.played = []

    def synthesize_play(self, reply, interrupt_check=lambda: False):
        self.played.append(reply)
        return True


def _silence_frames(n):
    return [np.zeros((1280, 1), dtype=np.float32) for _ in range(n)]


def _voice_config():
    cfg = AgentConfig.__new__(AgentConfig)
    for key, value in dict(
        sample_rate=16000,
        chunk_ms=80,
        capture_timeout_seconds=0.3,
        claps_required=2,
        clap_window_seconds=2.5,
        wake_phrase="vioris",
    ).items():
        setattr(cfg, key, value)
    return cfg


class TestVoiceLoopKillPhrase:
    def test_kill_phrase_preempts_planner_and_speaks_confirmation(self):
        state = AgentStateMachine()
        stop_calls: list[bool] = []

        def stop_fn():
            stop_calls.append(True)
            state.request_stop()
            return ["task_1"]

        listener = KillPhraseListener(stop_fn=stop_fn)
        tts = FakeTTS()
        stt = FakeSTT("stop everything")
        loop = VoiceLoop(
            state,
            FakeWake(activate_after=1),
            ClapDetector(required=2),
            tts,
            stt,
            ArraySourceInput(_silence_frames(8)),
            _voice_config(),
            kill_listener=listener,
        )

        loop._idle_wait()

        assert stop_calls == [True]
        assert listener.kill_count == 1
        assert stt.calls == 1
        assert loop.responses == [("stop everything", "Emergency stop executed. All tasks halted.")]
        assert tts.played == ["Emergency stop executed. All tasks halted."]
        assert any(ev.action == "stopped" for ev in loop.events)
        # The loop acknowledged the hard interrupt and is idle again.
        assert state.state == AgentState.IDLE
        assert state.interrupt_raised() is False

    def test_ordinary_transcript_still_reaches_the_command_handler(self):
        state = AgentStateMachine()
        listener = KillPhraseListener(stop_fn=lambda: state.request_stop() or [])
        loop = VoiceLoop(
            state,
            FakeWake(activate_after=1),
            ClapDetector(required=2),
            FakeTTS(),
            FakeSTT("what time is it"),
            ArraySourceInput(_silence_frames(8)),
            _voice_config(),
            kill_listener=listener,
        )

        loop._idle_wait()

        assert listener.kill_count == 0
        assert len(loop.responses) == 1
        assert "time" in loop.responses[0][1].lower()
