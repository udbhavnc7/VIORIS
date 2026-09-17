"""Tests for Phase 8.2 — kill phrase listener."""

import pytest

from apps.desktop_agent.app.services.kill_phrase import (
    DEFAULT_KILL_PHRASES,
    KillPhraseDetector,
    KillPhraseListener,
)


@pytest.fixture
def stop_log():
    """Records calls to the stop function."""
    calls = []

    def stop():
        calls.append(True)
        return ["task_1", "task_2"]

    return stop, calls


@pytest.fixture
def listener(stop_log):
    stop_fn, _ = stop_log
    return KillPhraseListener(stop_fn=stop_fn)


class TestKillPhraseListener:
    def test_exact_match(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("stop everything") is True
        assert len(calls) == 1

    def test_case_insensitive(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("STOP EVERYTHING") is True
        assert len(calls) == 1

    def test_substring_match(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("please stop everything now") is True
        assert len(calls) == 1

    def test_no_match(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("what time is it") is False
        assert len(calls) == 0

    def test_empty_transcript(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("") is False
        assert listener.check(None) is False  # type: ignore[arg-type]
        assert listener.check("   ") is False
        assert len(calls) == 0

    def test_vioris_stop(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("vioris stop") is True
        assert len(calls) == 1

    def test_emergency_stop(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("emergency stop") is True
        assert len(calls) == 1

    def test_abort_all(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("abort all") is True
        assert len(calls) == 1

    def test_cancel_everything(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("cancel everything") is True
        assert len(calls) == 1

    def test_halt(self, listener, stop_log):
        _, calls = stop_log
        assert listener.check("halt") is True
        assert len(calls) == 1

    def test_kill_count_tracks(self, listener):
        listener.check("stop everything")
        listener.check("abort all")
        assert listener.kill_count == 2

    def test_stop_fn_exception_does_not_crash(self):
        def bad_stop():
            raise RuntimeError("db offline")

        listener = KillPhraseListener(stop_fn=bad_stop)
        # Should not raise
        assert listener.check("stop everything") is True

    def test_stop_fn_returns_none(self):
        def none_stop():
            return None

        listener = KillPhraseListener(stop_fn=none_stop)
        assert listener.check("stop everything") is True


class TestCheckAndPreempt:
    def test_killed_returns_response(self, listener):
        was_killed, spoken = listener.check_and_preempt("stop everything")
        assert was_killed is True
        assert "halted" in spoken.lower() or "stop" in spoken.lower()

    def test_not_killed_returns_empty(self, listener):
        was_killed, spoken = listener.check_and_preempt("what time is it")
        assert was_killed is False
        assert spoken == ""


class TestKillPhraseDetector:
    def test_detect_true(self):
        det = KillPhraseDetector()
        assert det.detect("stop everything") is True

    def test_detect_false(self):
        det = KillPhraseDetector()
        assert det.detect("open vs code") is False

    def test_detect_case_insensitive(self):
        det = KillPhraseDetector()
        assert det.detect("STOP EVERYTHING") is True

    def test_custom_phrases(self):
        det = KillPhraseDetector(phrases=["shutdown now"])
        assert det.detect("shutdown now") is True
        assert det.detect("stop everything") is False

    def test_detect_empty(self):
        det = KillPhraseDetector()
        assert det.detect("") is False
        assert det.detect(None) is False  # type: ignore[arg-type]


class TestDefaultPhrases:
    def test_all_defaults_match(self):
        det = KillPhraseDetector()
        for phrase in DEFAULT_KILL_PHRASES:
            assert det.detect(phrase) is True, f"'{phrase}' should match"
