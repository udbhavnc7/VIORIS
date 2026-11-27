"""
Text-only interaction mode (Phase 10.1).

When STT is unavailable the agent must not just go silent. This module runs
commands from a text stream through the *same* permission-gated runner the
voice loop uses, so text mode obeys the frozen registry and audit trail.

Nothing here speaks or listens: the caller supplies the lines and an emitter.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

from packages.shared.graceful_degradation import DegradationMode, TextFallback

logger = logging.getLogger(__name__)

EXIT_WORDS = {"exit", "quit", ":q"}


def run_text_session(
    lines: Iterable[str],
    emit: Callable[[str], None],
    mode: DegradationMode,
    runner: Callable[[str], dict],
    kill_listener=None,
) -> list[dict]:
    """Process text commands one at a time; return each fallback result.

    `runner` is normally `command_runner.run_command` partially applied with
    the executor and store. Kill phrases are checked before the runner, exactly
    as in the voice loop, and end the session after the confirmation is emitted.
    """
    fallback = TextFallback(mode, command_runner=runner)
    results: list[dict] = []

    for raw in lines:
        if raw is None:
            break
        text = raw.strip()
        if not text:
            continue
        if text.lower() in EXIT_WORDS:
            break

        if kill_listener is not None:
            killed, reply = kill_listener.check_and_preempt(text)
            if killed:
                emit(reply)
                break

        result = fallback.handle(text)
        results.append(result)
        emit(result["reply"])

    return results
