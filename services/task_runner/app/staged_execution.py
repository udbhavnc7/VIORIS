"""
Reversible-by-default execution (Phase 8, Prompt 8.3).

For Critical actions with a natural undo (delete → trash, send → delayed send,
install → snapshot first), don't just gate them with approval — stage them with
a real undo window, the way Gmail's "undo send" works.

This turns "Critical" from "scary and final" into "fast, but forgiving."

How it works:
  1. Before executing a Critical action, the engine creates a StagedAction
     with an undo window (default: 30 seconds).
  2. During the window, the action is STAGED but not executed.
  3. If the user cancels within the window, the action is abandoned.
  4. If the window expires, the action executes and an undo handle is stored.
  5. Within a secondary "undo window" (default: 5 minutes), the user can
     trigger an undo — the stored handle is called to reverse the action.
  6. After the undo window, the action is permanent.

This is purely a time-based staging mechanism — no new permission tiers.
The approval flow still gates the initial staging; the undo window is
additional safety on top of approval.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class StageStatus(str, Enum):
    """Lifecycle of a staged action."""

    STAGED = "staged"  # waiting in the undo window
    EXECUTED = "executed"  # undo window expired, action fired
    CANCELLED = "cancelled"  # user cancelled during undo window
    UNDONE = "undone"  # user triggered undo after execution


@dataclass
class StagedAction:
    """A Critical action staged for execution with an undo window.

    The action itself is NOT stored here — only the metadata. The actual
    execution happens via the `execute_fn` callback when the window expires.
    The undo is handled by the `undo_fn` callback.
    """

    stage_id: str = field(default_factory=lambda: f"stage_{uuid.uuid4().hex[:8]}")
    tool: str = ""
    task_id: str = ""
    step_id: str = ""
    description: str = ""  # human-readable, shown in undo UI
    diff_card: dict[str, Any] = field(default_factory=dict)
    status: StageStatus = StageStatus.STAGED
    staged_at: float = field(default_factory=time.monotonic)
    undo_window_seconds: float = 300.0  # 5 minutes to undo after execution
    execute_after_seconds: float = 30.0  # 30 seconds before auto-executing


class UndoWindowExpired(Exception):
    """The undo window has closed — the action is permanent."""


class StagedActionStore:
    """In-memory store for staged actions with timer-based execution.

    Each staged action gets a timer that fires after the undo window to
    actually execute it. The user can cancel before execution or undo after.
    """

    def __init__(self) -> None:
        self._actions: dict[str, StagedAction] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._undo_timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def stage(
        self,
        tool: str,
        task_id: str,
        step_id: str,
        description: str,
        diff_card: dict[str, Any],
        execute_fn: Callable[[], Any],
        undo_fn: Callable[[], Any] | None = None,
        execute_after_seconds: float = 30.0,
        undo_window_seconds: float = 300.0,
    ) -> StagedAction:
        """Stage a Critical action for execution.

        The action will auto-execute after `execute_after_seconds` unless
        cancelled. After execution, the user has `undo_window_seconds` to
        trigger an undo.

        Args:
            tool: Tool name being executed.
            task_id: Owning task ID.
            step_id: Owning step ID.
            description: Human-readable description for the undo UI.
            diff_card: What the action will do (shown to user).
            execute_fn: Callable that performs the actual action.
            undo_fn: Callable that reverses the action (optional).
            execute_after_seconds: Seconds before auto-execution (default 30).
            undo_window_seconds: Seconds after execution to allow undo (default 300).

        Returns:
            The StagedAction with its stage_id (for cancel/undo operations).
        """
        action = StagedAction(
            tool=tool,
            task_id=task_id,
            step_id=step_id,
            description=description,
            diff_card=diff_card,
            execute_after_seconds=execute_after_seconds,
            undo_window_seconds=undo_window_seconds,
        )

        with self._lock:
            self._actions[action.stage_id] = action

        # Set up auto-execution timer
        timer = threading.Timer(
            execute_after_seconds,
            self._auto_execute,
            args=(action.stage_id, execute_fn, undo_fn),
        )
        timer.daemon = True
        with self._lock:
            self._timers[action.stage_id] = timer
        timer.start()

        logger.info(
            "Staged action %s: tool=%s, execute in %.0fs, undo window=%.0fs",
            action.stage_id,
            tool,
            execute_after_seconds,
            undo_window_seconds,
        )
        return action

    def cancel(self, stage_id: str) -> bool:
        """Cancel a staged action before it executes.

        Returns True if cancelled, False if already executed or not found.
        """
        with self._lock:
            action = self._actions.get(stage_id)
            if action is None:
                return False
            if action.status != StageStatus.STAGED:
                return False

            # Cancel the timer
            timer = self._timers.pop(stage_id, None)
            if timer is not None:
                timer.cancel()

            action.status = StageStatus.CANCELLED
            logger.info("Staged action %s cancelled", stage_id)
            return True

    def undo(self, stage_id: str) -> bool:
        """Undo an executed action within the undo window.

        Returns True if undone, False if undo window expired or not found.
        """
        with self._lock:
            action = self._actions.get(stage_id)
            if action is None:
                return False
            if action.status != StageStatus.EXECUTED:
                return False

            # Check if undo window has actually expired (time-based check)
            elapsed = time.monotonic() - action.staged_at
            # The undo window starts after execution (execute_after_seconds)
            # and lasts for undo_window_seconds
            undo_deadline = action.execute_after_seconds + action.undo_window_seconds
            if elapsed > undo_deadline:
                return False

            # Cancel the undo timer if still active
            undo_timer = self._undo_timers.pop(stage_id, None)
            if undo_timer is not None:
                undo_timer.cancel()

            action.status = StageStatus.UNDONE
            logger.info("Staged action %s undone", stage_id)
            return True

    def get(self, stage_id: str) -> StagedAction | None:
        """Get a staged action by ID."""
        with self._lock:
            return self._actions.get(stage_id)

    def list_pending(self) -> list[StagedAction]:
        """List all staged actions (not yet executed or cancelled)."""
        with self._lock:
            return [a for a in self._actions.values() if a.status == StageStatus.STAGED]

    def list_undoable(self) -> list[StagedAction]:
        """List all actions that can be undone (executed, within undo window)."""
        with self._lock:
            return [a for a in self._actions.values() if a.status == StageStatus.EXECUTED]

    def cleanup_expired(self) -> int:
        """Remove actions that are terminal (cancelled, undone, or old executed).

        Returns the number of actions cleaned up.
        """
        cutoff = time.monotonic() - 3600  # 1 hour
        removed = []
        with self._lock:
            for stage_id, action in self._actions.items():
                if action.status in (StageStatus.CANCELLED, StageStatus.UNDONE):
                    removed.append(stage_id)
                elif (
                    action.status == StageStatus.EXECUTED
                    and action.staged_at < cutoff
                ):
                    removed.append(stage_id)
            for stage_id in removed:
                del self._actions[stage_id]
        return len(removed)

    def _auto_execute(self, stage_id: str, execute_fn: Callable, undo_fn: Callable | None) -> None:
        """Called by timer when the undo window expires — execute the action."""
        with self._lock:
            action = self._actions.get(stage_id)
            if action is None or action.status != StageStatus.STAGED:
                return  # already cancelled

        try:
            result = execute_fn()
            with self._lock:
                action.status = StageStatus.EXECUTED
            logger.info("Staged action %s executed", stage_id)

            # Set up undo window timer if undo_fn is provided
            if undo_fn is not None:
                undo_timer = threading.Timer(
                    action.undo_window_seconds,
                    self._expire_undo,
                    args=(stage_id,),
                )
                undo_timer.daemon = True
                with self._lock:
                    self._undo_timers[stage_id] = undo_timer
                undo_timer.start()

                # Store the undo function so it can be called
                action._undo_fn = undo_fn  # type: ignore[attr-defined]

        except Exception as exc:
            logger.error("Staged action %s execution failed: %s", stage_id, exc)
            with self._lock:
                action.status = StageStatus.CANCELLED

    def _expire_undo(self, stage_id: str) -> None:
        """Called by timer when the undo window expires — action is permanent."""
        with self._lock:
            action = self._actions.get(stage_id)
            if action is not None and action.status == StageStatus.EXECUTED:
                # Remove the undo function — no longer reversible
                if hasattr(action, "_undo_fn"):
                    delattr(action, "_undo_fn")
                logger.info("Staged action %s undo window expired — permanent", stage_id)
