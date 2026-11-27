"""Tests for text-only degradation wiring (Phase 10.1)."""

import numpy as np
import pytest

from apps.desktop_agent.app.command_runner import run_command
from apps.desktop_agent.app.config import AgentConfig
from apps.desktop_agent.app.executor import Executor
from apps.desktop_agent.app.services.audio_source import ArraySourceInput
from apps.desktop_agent.app.services.kill_phrase import KillPhraseListener
from apps.desktop_agent.app.services.state_machine import AgentStateMachine
from apps.desktop_agent.app.services.text_mode import run_text_session
from apps.desktop_agent.app.services.voice_loop import VoiceLoop
from apps.desktop_agent.app.services.wake_detector import ClapDetector
from apps.desktop_agent.app.store import TaskStore
from packages.shared.graceful_degradation import DegradationMode, SubsystemStatus
from packages.shared.permission_engine import PermissionEngine, register_phase1_tools


@pytest.fixture(autouse=True)
def _registry():
    PermissionEngine.reset()
    register_phase1_tools()
    PermissionEngine.freeze()
    yield


def _text_mode() -> DegradationMode:
    mode = DegradationMode()
    mode.stt.status = SubsystemStatus.UNAVAILABLE
    return mode


class TestTextSession:
    def test_command_uses_same_permission_gated_runner(self, tmp_path):
        store = TaskStore(tmp_path / "agent.db")
        emitted: list[str] = []
        mode = _text_mode()

        def runner(text):
            return run_command(text, Executor(), store)

        results = run_text_session(
            ["what time is it", "exit"], emitted.append, mode, runner
        )

        assert len(results) == 1
        assert results[0]["tool"] == "system.get_time"
        assert results[0]["mode"] == "text_only"
        assert "time" in emitted[0].lower()
        assert store.list_tasks(), "text mode must record the task and audit trail"

    def test_blank_lines_and_exit_word_are_ignored(self, tmp_path):
        store = TaskStore(tmp_path / "agent.db")
        emitted: list[str] = []

        results = run_text_session(
            ["", "   ", "QUIT", "what time is it"],
            emitted.append,
            _text_mode(),
            lambda text: run_command(text, Executor(), store),
        )
        assert results == []
        assert emitted == []

    def test_kill_phrase_preempts_runner(self):
        emitted: list[str] = []
        stopped: list[bool] = []
        listener = KillPhraseListener(stop_fn=lambda: stopped.append(True))

        def runner(text):
            raise AssertionError("runner must not see a kill phrase")

        results = run_text_session(
            ["stop everything", "what time is it"], emitted.append, _text_mode(), runner, listener
        )
        assert results == []
        assert stopped == [True]
        assert "Emergency stop" in emitted[0]

    def test_runner_failure_is_reported_not_silent(self):
        emitted: list[str] = []

        def runner(text):
            raise RuntimeError("Ollama is down")

        results = run_text_session(["hello"], emitted.append, _text_mode(), runner)
        assert results[0]["status"] == "error"
        assert "Ollama" in emitted[0]


class TestVoiceLoopDegradation:
    def test_stt_failure_marks_mode_text_only(self):
        cfg = AgentConfig.__new__(AgentConfig)
        cfg.sample_rate = 16000
        cfg.chunk_ms = 80
        cfg.capture_timeout_seconds = 0.2
        cfg.claps_required = 2
        cfg.clap_window_seconds = 2.5
        cfg.wake_phrase = "vioris"

        class BrokenSTT:
            def transcribe(self, audio):
                raise RuntimeError("faster-whisper missing")

        wake = type(
            "FakeWake",
            (),
            {
                "frame_size": 1280,
                "load": lambda self: None,
                "is_activated": lambda self, frame: True,
            },
        )()
        source = ArraySourceInput([np.zeros((1280, 1), dtype=np.float32) for _ in range(5)])
        mode = DegradationMode()
        loop = VoiceLoop(
            AgentStateMachine(),
            wake,
            ClapDetector(required=2),
            type("FakeTTS", (), {"synthesize_play": lambda *a, **k: True})(),
            BrokenSTT(),
            source,
            cfg,
            mode=mode,
        )

        loop._idle_wait()

        assert mode.stt.status == SubsystemStatus.UNAVAILABLE
        assert mode.is_text_only
        assert any("text mode" in reply.lower() for _, reply in loop.responses)
