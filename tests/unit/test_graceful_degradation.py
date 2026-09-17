"""Tests for graceful degradation (Phase 10.1)."""

import pytest

from packages.shared.graceful_degradation import (
    DegradationMode,
    SubsystemHealth,
    SubsystemStatus,
    InteractionMode,
    TextFallback,
)


class TestSubsystemHealth:
    def test_healthy_is_usable(self):
        h = SubsystemHealth(name="stt", status=SubsystemStatus.HEALTHY)
        assert h.is_usable is True

    def test_degraded_is_usable(self):
        h = SubsystemHealth(name="stt", status=SubsystemStatus.DEGRADED)
        assert h.is_usable is True

    def test_unavailable_is_not_usable(self):
        h = SubsystemHealth(name="stt", status=SubsystemStatus.UNAVAILABLE)
        assert h.is_usable is False


class TestDegradationMode:
    def test_all_healthy_is_voice(self):
        mode = DegradationMode()
        assert mode.mode == InteractionMode.VOICE
        assert not mode.is_text_only
        assert not mode.is_degraded

    def test_stt_unavailable_is_text_only(self):
        mode = DegradationMode()
        mode.stt.status = SubsystemStatus.UNAVAILABLE
        assert mode.mode == InteractionMode.TEXT_ONLY
        assert mode.is_text_only

    def test_tts_unavailable_is_degraded(self):
        mode = DegradationMode()
        mode.tts.status = SubsystemStatus.UNAVAILABLE
        assert mode.mode == InteractionMode.VOICE_BUT_DEGRADED
        assert mode.is_degraded
        assert not mode.is_text_only

    def test_wake_unavailable_is_degraded(self):
        mode = DegradationMode()
        mode.wake.status = SubsystemStatus.UNAVAILABLE
        assert mode.mode == InteractionMode.VOICE_BUT_DEGRADED

    def test_wake_degraded_still_voice(self):
        """Wake DEGRADED (clap-only) doesn't degrade overall mode — agent still works."""
        mode = DegradationMode()
        mode.wake.status = SubsystemStatus.DEGRADED
        assert mode.mode == InteractionMode.VOICE

    def test_stt_unavailable_takes_precedence(self):
        """If STT is down, it doesn't matter that TTS works — can't hear."""
        mode = DegradationMode()
        mode.stt.status = SubsystemStatus.UNAVAILABLE
        mode.tts.status = SubsystemStatus.HEALTHY
        assert mode.mode == InteractionMode.TEXT_ONLY


class TestHealthChecks:
    def test_check_stt_with_transcribe(self):
        mode = DegradationMode()
        fake_stt = type("FakeSTT", (), {"transcribe": lambda self, a: ""})()
        result = mode.check_stt(fake_stt)
        assert result.status == SubsystemStatus.HEALTHY

    def test_check_stt_without_transcribe(self):
        mode = DegradationMode()
        fake_stt = type("FakeSTT", (), {})()
        result = mode.check_stt(fake_stt)
        assert result.status == SubsystemStatus.UNAVAILABLE

    def test_check_stt_with_exception(self):
        mode = DegradationMode()

        class BadSTT:
            @property
            def model(self):
                raise RuntimeError("CUDA out of memory")

        result = mode.check_stt(BadSTT())
        assert result.status == SubsystemStatus.UNAVAILABLE
        assert "CUDA" in result.error

    def test_check_tts_with_synthesize(self):
        mode = DegradationMode()
        fake_tts = type("FakeTTS", (), {"synthesize": lambda self, t: iter([])})()
        result = mode.check_tts(fake_tts)
        assert result.status == SubsystemStatus.HEALTHY

    def test_check_tts_without_synthesize(self):
        mode = DegradationMode()
        fake_tts = type("FakeTTS", (), {})()
        result = mode.check_tts(fake_tts)
        assert result.status == SubsystemStatus.UNAVAILABLE

    def test_check_wake_with_score_frame(self):
        mode = DegradationMode()
        fake_wake = type("FakeWake", (), {"score_frame": lambda self, f: 0.0})()
        result = mode.check_wake(fake_wake)
        assert result.status == SubsystemStatus.HEALTHY

    def test_check_wake_without_methods(self):
        mode = DegradationMode()
        fake_wake = type("FakeWake", (), {})()
        result = mode.check_wake(fake_wake)
        assert result.status == SubsystemStatus.DEGRADED

    def test_health_check_caching(self):
        mode = DegradationMode()
        mode.health_check_interval = 9999  # very long cache
        fake_stt = type("FakeSTT", (), {"transcribe": lambda self, a: ""})()

        first = mode.check_stt(fake_stt)
        first.status = SubsystemStatus.UNAVAILABLE  # mutate
        second = mode.check_stt(fake_stt)
        # Should return cached (same object)
        assert second.status == SubsystemStatus.UNAVAILABLE

    def test_check_all(self):
        mode = DegradationMode()
        fake_stt = type("FakeSTT", (), {"transcribe": lambda self, a: ""})()
        fake_tts = type("FakeTTS", (), {"synthesize": lambda self, t: iter([])})()
        fake_wake = type("FakeWake", (), {"score_frame": lambda self, f: 0.0})()

        result = mode.check_all(stt=fake_stt, tts=fake_tts, wake=fake_wake)
        assert result == InteractionMode.VOICE


class TestSummary:
    def test_summary_shape(self):
        mode = DegradationMode()
        s = mode.summary()
        assert "mode" in s
        assert "stt" in s
        assert "tts" in s
        assert "wake" in s
        assert s["mode"] == "voice"
        assert s["stt"]["status"] == "healthy"


class TestTextFallback:
    def test_empty_input(self):
        mode = DegradationMode()
        fb = TextFallback(mode=mode)
        result = fb.handle("")
        assert result["status"] == "error"
        assert "No input" in result["reply"]

    def test_none_input(self):
        mode = DegradationMode()
        fb = TextFallback(mode=mode)
        result = fb.handle(None)
        assert result["status"] == "error"

    def test_with_command_runner(self):
        mode = DegradationMode()

        def runner(text):
            return {"reply": f"Time is 3pm", "tool": "system.get_time", "status": "ok"}

        fb = TextFallback(mode=mode, command_runner=runner)
        result = fb.handle("what time is it")
        assert result["reply"] == "Time is 3pm"
        assert result["tool"] == "system.get_time"
        assert result["status"] == "ok"

    def test_command_runner_exception(self):
        mode = DegradationMode()

        def runner(text):
            raise RuntimeError("Ollama is down")

        fb = TextFallback(mode=mode, command_runner=runner)
        result = fb.handle("do something")
        assert result["status"] == "error"
        assert "Ollama" in result["reply"]

    def test_no_command_runner(self):
        mode = DegradationMode()
        fb = TextFallback(mode=mode)
        result = fb.handle("hello")
        assert result["status"] == "ok"
        assert "Text mode" in result["reply"]

    def test_is_active_when_text_only(self):
        mode = DegradationMode()
        mode.stt.status = SubsystemStatus.UNAVAILABLE
        fb = TextFallback(mode=mode)
        assert fb.is_active is True

    def test_is_not_active_when_voice(self):
        mode = DegradationMode()
        fb = TextFallback(mode=mode)
        assert fb.is_active is False

    def test_mode_reflected_in_result(self):
        mode = DegradationMode()
        mode.stt.status = SubsystemStatus.UNAVAILABLE
        fb = TextFallback(mode=mode)
        result = fb.handle("test")
        assert result["mode"] == "text_only"
