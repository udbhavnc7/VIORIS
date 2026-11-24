"""Backward-compatible import path for the shared kill-phrase module.

The implementation moved to ``packages/shared/kill_phrase.py`` so the task
runner and API gateway can enforce the same kill phrases without importing the
desktop agent. Existing callers can keep importing from here.
"""

from packages.shared.kill_phrase import (  # noqa: F401
    DEFAULT_KILL_PHRASES,
    KillPhraseDetector,
    KillPhraseListener,
    _build_kill_pattern,
    detect_kill_phrase,
)

__all__ = [
    "DEFAULT_KILL_PHRASES",
    "KillPhraseDetector",
    "KillPhraseListener",
    "detect_kill_phrase",
]
