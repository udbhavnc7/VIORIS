"""
Kill-phrase listener (Phase 8, Prompt 8.2).

A dead-simple, LLM-independent listener that force-halts all in-progress
tasks when a kill phrase is spoken. This runs at the wake-word layer —
BEFORE the planner/LLM pipeline — so a confused or hijacked plan can
always be killed even if the LLM itself is behaving strangely.

How it works:
  - The kill listener runs as a separate thread alongside the main voice loop.
  - It uses the same audio source as the wake-word detector.
  - When the wake-word layer activates and the transcribed text matches a
    kill phrase, it calls the emergency stop directly — no planning, no
    permission gate, no LLM involvement.
  - The kill phrase detection is simple string matching (not LLM-based) to
    minimize latency and maximize reliability.

Kill phrases are configurable. Defaults: "stop everything", "vioris stop",
"emergency stop", "abort all".
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# Default kill phrases — case-insensitive, matched against raw transcript.
DEFAULT_KILL_PHRASES = [
    "stop everything",
    "vioris stop",
    "emergency stop",
    "abort all",
    "cancel everything",
    "halt",
]

# Compiled pattern: matches any of the kill phrases as a substring.
# Uses word boundaries where possible but falls back to substring for short
# phrases like "halt" to catch "halt!" or "halt now".
_KILL_PATTERN_TEMPLATE = r"(?:{alternatives})"
_KILL_FLAGS = re.IGNORECASE | re.UNICODE


def _build_kill_pattern(phrases: list[str]) -> re.Pattern[str]:
    """Build a regex that matches any kill phrase as a substring."""
    # Escape each phrase for regex safety, join with OR
    alternatives = "|".join(re.escape(p) for p in phrases)
    pattern = _KILL_PATTERN_TEMPLATE.format(alternatives=alternatives)
    return re.compile(pattern, _KILL_FLAGS)


class KillPhraseListener:
    """Listens for kill phrases in transcripts and triggers emergency stop.

    This operates at the wake-word layer level — it does NOT go through the
    LLM planner. When a kill phrase is detected, it calls the provided
    stop_fn immediately.

    Usage:
        listener = KillPhraseListener(stop_fn=my_emergency_stop)
        # In the voice loop, after transcription:
        if listener.check(transcript):
            return  # kill was handled, don't proceed to planner
    """

    def __init__(
        self,
        stop_fn: Callable[[], list[str] | None],
        phrases: list[str] | None = None,
    ) -> None:
        """
        Args:
            stop_fn: Callable that performs the emergency stop. Called
                     synchronously in the voice loop thread. Returns a list
                     of affected task IDs (or None).
            phrases: Custom kill phrases. Defaults to DEFAULT_KILL_PHRASES.
        """
        self.phrases = phrases or DEFAULT_KILL_PHRASES
        self.stop_fn = stop_fn
        self._pattern = _build_kill_pattern(self.phrases)
        self._kill_count = 0  # tracks how many times kill has been triggered

    def check(self, transcript: str) -> bool:
        """Check if a transcript contains a kill phrase.

        Returns True if a kill phrase was detected (and stop was triggered).
        Returns False if the transcript is safe to proceed to the planner.
        """
        if not transcript or not transcript.strip():
            return False

        text = transcript.strip()
        if self._pattern.search(text):
            logger.warning(
                "KILL PHRASE DETECTED in transcript: '%s' — triggering emergency stop",
                text,
            )
            self._kill_count += 1
            try:
                affected = self.stop_fn()
                if affected:
                    logger.warning(
                        "Emergency stop completed: %d tasks halted (kill #%d)",
                        len(affected),
                        self._kill_count,
                    )
                else:
                    logger.info("Emergency stop triggered (no active tasks) (kill #%d)", self._kill_count)
            except Exception as exc:
                logger.error("Emergency stop failed: %s", exc)
            return True

        return False

    def check_and_preempt(self, transcript: str) -> tuple[bool, str]:
        """Like check() but also returns a spoken response for TTS.

        Returns (was_killed: bool, spoken_response: str).
        If killed, the response is a confirmation spoken to the user.
        """
        if self.check(transcript):
            return True, "Emergency stop executed. All tasks halted."
        return False, ""

    @property
    def kill_count(self) -> int:
        """Number of times the kill phrase has been triggered."""
        return self._kill_count

    def matches(self, transcript: str) -> bool:
        """Check if a transcript matches a kill phrase without triggering stop.
        Useful for testing or previewing.
        """
        if not transcript or not transcript.strip():
            return False
        return bool(self._pattern.search(transcript.strip()))


class KillPhraseDetector:
    """Standalone kill-phrase detection for the wake-word layer.

    Unlike KillPhraseListener, this is a pure detector that doesn't trigger
    any action — it just reports whether a kill phrase was heard. This is
    useful when the kill check needs to happen before the voice loop decides
    whether to route to the planner or to the kill handler.
    """

    def __init__(self, phrases: list[str] | None = None) -> None:
        self.phrases = phrases or DEFAULT_KILL_PHRASES
        self._pattern = _build_kill_pattern(self.phrases)

    def detect(self, transcript: str) -> bool:
        """Returns True if the transcript contains a kill phrase."""
        if not transcript or not transcript.strip():
            return False
        return bool(self._pattern.search(transcript.strip()))
