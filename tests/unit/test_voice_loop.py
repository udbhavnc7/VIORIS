import numpy as np

from apps.desktop_agent.app.config import AgentConfig
from apps.desktop_agent.app.services.audio_source import ArraySourceInput
from apps.desktop_agent.app.services.state_machine import AgentStateMachine
from apps.desktop_agent.app.services.voice_loop import VoiceLoop
from apps.desktop_agent.app.services.wake_detector import ClapDetector
from packages.shared.schemas import AgentState


class FakeWake:
    """Wake detector that activates on a fixed frame, once."""

    frame_size = 1280
    threshold = 0.5

    def __init__(self, activate_after=1):
        self._count = 0
        self._activate_after = activate_after
        self.loaded = False

    def load(self):
        self.loaded = True

    def is_activated(self, frame):
        self._count += 1
        return self._count > self._activate_after


class FakeSTT:
    def __init__(self, transcript="what time is it"):
        self.transcript = transcript
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return self.transcript


class FakeTTS:
    def __init__(self):
        self.played = []
        self.chunk_calls = 0

    def synthesize_play(self, reply, interrupt_check=lambda: False):
        self.played.append(reply)
        self.chunk_calls += 1
        return True


def silence_frames(n, sr=16000):
    """n frames of 1280 samples (80ms @ 16kHz), mono float32."""
    return [np.zeros((1280, 1), dtype=np.float32) for _ in range(n)]


def make_config(**overrides):
    base = dict(
        sample_rate=16000,
        chunk_ms=80,
        capture_timeout_seconds=0.3,
        claps_required=2,
        clap_window_seconds=2.5,
        wake_phrase="vioris",
    )
    base.update(overrides)
    cfg = AgentConfig.__new__(AgentConfig)
    for k, v in base.items():
        setattr(cfg, k, v)
    return cfg


class TestWakeRoundTrip:
    def test_wake_then_transcribe_then_speak(self):
        cfg = make_config()
        source = ArraySourceInput(silence_frames(8))
        wake = FakeWake(activate_after=1)
        clap = ClapDetector(required=2)
        stt = FakeSTT("what time is it")
        tts = FakeTTS()
        sm = AgentStateMachine()
        loop = VoiceLoop(sm, wake, clap, tts, stt, source, cfg)

        loop._idle_wait()

        assert len(loop.responses) == 1
        text, reply = loop.responses[0]
        assert text == "what time is it"
        assert "time" in reply.lower()
        assert tts.played, "TTS should have spoken a reply"
        assert stt.calls == 1

    def test_wake_activity_is_audited(self):
        cfg = make_config()
        source = ArraySourceInput(silence_frames(8))
        loop = VoiceLoop(
            AgentStateMachine(),
            FakeWake(activate_after=1),
            ClapDetector(required=2),
            FakeTTS(),
            FakeSTT(),
            source,
            cfg,
        )
        loop._idle_wait()
        assert any(ev.action == "wake_activated" for ev in loop.events)
        assert loop.events[0].prev_hash == "0" * 64  # genesis-chained

    def test_no_wake_no_response(self):
        cfg = make_config()
        source = ArraySourceInput(silence_frames(4))
        loop = VoiceLoop(
            AgentStateMachine(),
            FakeWake(activate_after=1000),
            ClapDetector(required=2),
            FakeTTS(),
            FakeSTT(),
            source,
            cfg,
        )
        loop._idle_wait()
        assert loop.responses == []


class TestStopMidSpeech:
    def test_stop_interrupt_cancels_before_play(self):
        from apps.desktop_agent.app.commands import handle

        cfg = make_config()
        source = ArraySourceInput(silence_frames(8))
        tts = FakeTTS()
        sm = AgentStateMachine()
        loop = VoiceLoop(
            sm,
            FakeWake(activate_after=1),
            ClapDetector(required=2),
            tts,
            FakeSTT("stop"),
            source,
            cfg,
        )

        result = handle("stop")
        # hard stop requested -> speaking must not start (no reply spoken)
        sm.request_stop()
        loop._speak(result.reply)
        assert sm.interrupt_raised()
        assert not tts.played, "TTS must not play after a hard stop"
        assert sm.state == AgentState.STOPPED


class TestClapPattern:
    def test_two_claps_within_window_activate(self):
        clap = ClapDetector(required=2, window_seconds=2.5, energy_threshold=0.1)
        loud = np.full((1280, 1), 0.9, dtype=np.float32)
        quiet = np.zeros((1280, 1), dtype=np.float32)
        assert clap.detect(loud) is False
        assert clap.detect(quiet) is False
        assert clap.detect(loud) is True

    def test_single_clap_alone_never_activates(self):
        clap = ClapDetector(required=2, window_seconds=2.5, energy_threshold=0.1)
        loud = np.full((1280, 1), 0.9, dtype=np.float32)
        quiet = np.zeros((1280, 1), dtype=np.float32)
        assert clap.detect(loud) is False
        assert clap.detect(quiet) is False
        assert clap.detect(quiet) is False  # still only one clap recorded
        assert clap.detect(quiet) is False
