"""
Desktop agent state machine with hard interrupt (Phase 1).

States come from `packages.shared.schemas.AgentState`. Transitions are kept
strict; the `stop` interrupt is a threading.Event checked by every long-running
phase (STT capture, TTS playback) so "stop"/"Vioris stop" can cancel an
in-progress response within ~500ms — it is a hard interrupt at the task-runner
level, not something the LLM must "notice" (docs/03 §5).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from packages.shared.schemas import AgentState

logger = logging.getLogger(__name__)

# Allowed transitions; anything else raises StateTransitionError.
_ALLOWED: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {
        AgentState.LISTENING,
        AgentState.THINKING,
        AgentState.STOPPED,
        AgentState.ERROR,
    },
    AgentState.LISTENING: {
        AgentState.THINKING,
        AgentState.STOPPED,
        AgentState.ERROR,
        AgentState.IDLE,
    },
    AgentState.THINKING: {
        AgentState.SPEAKING,
        AgentState.LISTENING,
        AgentState.IDLE,
        AgentState.STOPPED,
        AgentState.ERROR,
    },
    AgentState.SPEAKING: {
        AgentState.IDLE,
        AgentState.LISTENING,
        AgentState.STOPPED,
        AgentState.ERROR,
    },
    AgentState.ERROR: {AgentState.IDLE, AgentState.STOPPED},
    AgentState.STOPPED: {AgentState.IDLE, AgentState.ERROR},
}


class StateTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""


class AgentStateMachine:
    """Thread-safe state machine with a hard interrupt event."""

    def __init__(self, on_change: Callable[[AgentState], None] | None = None) -> None:
        self._state = AgentState.IDLE
        self._lock = threading.Lock()
        self._stop_event = threading.Event()  # set() == a stop was requested
        self._on_change = on_change

    @property
    def state(self) -> AgentState:
        with self._lock:
            return self._state

    def allowed(self, target: AgentState) -> bool:
        return target in _ALLOWED[self.state]

    def transition(self, target: AgentState) -> None:
        with self._lock:
            current = self._state
            if target == current:
                return  # idempotent no-op
            if target not in _ALLOWED[current]:
                raise StateTransitionError(f"Illegal transition {current.value} -> {target.value}")
            self._state = target
        logger.info("state: %s -> %s", current.value, target.value)
        if self._on_change is not None:
            self._on_change(target)

    # ── convenience transitions ──────────────────────────────────────────
    def to_idle(self) -> None:
        self.transition(AgentState.IDLE)

    def to_listening(self) -> None:
        self.transition(AgentState.LISTENING)

    def to_thinking(self) -> None:
        self.transition(AgentState.THINKING)

    def to_speaking(self) -> None:
        self.transition(AgentState.SPEAKING)

    def to_error(self) -> None:
        self.transition(AgentState.ERROR)

    # ── hard interrupt ───────────────────────────────────────────────────
    def request_stop(self) -> None:
        """Signal every long-running phase to cancel ASAP.
        Safe to call from any thread at any time; idempotent.
        A stop always lands the machine in STOPPED so the UI shows a
        deterministic state, regardless of when it fires.
        """
        self._stop_event.set()
        with self._lock:
            if self._state != AgentState.STOPPED:
                self._state = AgentState.STOPPED
        logger.info("hard interrupt requested; state -> %s", self.state.value)
        if self._on_change is not None:
            self._on_change(self.state)

    def interrupt_raised(self) -> bool:
        """True if a stop was requested and not acknowledged."""
        return self._stop_event.is_set()

    def acknowledge_stop(self) -> None:
        """Mark the interrupt as serviced. Used when a phase finishes."""
        self._stop_event.clear()

    def clear_stop(self) -> None:
        self._stop_event.clear()
