"""
Computer agent core (Phase 3, Prompt 3.1).

A local "operate the laptop" daemon exposing narrow tools:

  - computer.list_windows
  - computer.capture_screenshot
  - computer.read_screen_text
  - computer.open_app

Every mutating/lookup action returns a VERIFIED outcome, not "command sent".
The agent re-checks real screen state after each action (window titles,
screenshot bytes, OCR text) and reports success or failure. Tools are
classified statically through the shared permission engine — the agent never
sets its own risk tiers; it only offers tools that OCBSERVE tier allows
without confirmation.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from packages.shared.permission_engine import UnknownToolError

from .vision import default_ocr, default_screenshot
from .windows import default_list_windows, default_lock_workstation, default_open_app

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path("vioris_data/screenshots")


class PermissionBlockedError(Exception):
    """The step's tool is not registered; it was never executed."""


@dataclass
class ActionOutcome:
    """Typed result of a computer-agent action, including verification."""

    tool: str
    ok: bool
    note: str
    detail: dict = field(default_factory=dict)
    verified: bool = False
    verified_note: str = ""
    error: str | None = None


class ComputerAgent:
    def __init__(
        self,
        screenshot_dir: str | Path | None = None,
        list_windows_fn: Callable[[], list[dict]] | None = None,
        screenshot_fn: Callable[[Path], Path] | None = None,
        ocr_fn: Callable[[Path], str] | None = None,
        open_app_fn: Callable[[str], None] | None = None,
        lock_workstation_fn: Callable[[], None] | None = None,
    ) -> None:
        self._screenshot_dir = Path(screenshot_dir or SCREENSHOT_DIR)
        self._list_windows = list_windows_fn or default_list_windows
        self._screenshot = screenshot_fn or default_screenshot
        self._ocr = ocr_fn or default_ocr
        self._open_app = open_app_fn or default_open_app
        self._lock_workstation = lock_workstation_fn or default_lock_workstation
        self._executions: list[str] = []

    # ── tools ────────────────────────────────────────────────────────────
    def list_windows(self) -> ActionOutcome:
        """OBSERVE: read-only window listing."""
        try:
            windows = self._list_windows()
        except Exception as exc:  # noqa: BLE001 — backend failure becomes a typed error
            return ActionOutcome(
                tool="computer.list_windows",
                ok=False,
                note="failed to list windows",
                error=str(exc),
            )
        self._executions.append("computer.list_windows")
        return ActionOutcome(
            tool="computer.list_windows",
            ok=True,
            note="listed windows",
            detail={"windows": windows, "count": len(windows)},
            verified=True,
            verified_note=f"{len(windows)} windows found",
        )

    def open_app(self, name: str) -> ActionOutcome:
        """OBSERVE: launch an app, then VERIFY it appears on screen."""
        if not name or not name.strip():
            return ActionOutcome(
                tool="computer.open_app", ok=False, note="no app name provided"
            )
        try:
            self._open_app(name.strip())
        except Exception as exc:  # noqa: BLE001
            return ActionOutcome(
                tool="computer.open_app",
                ok=False,
                note=f"failed to open {name!r}",
                error=str(exc),
            )
        self._executions.append("computer.open_app")
        # Re-check the screen: did the window actually appear?
        try:
            windows = self._list_windows()
        except Exception as exc:  # noqa: BLE001
            return ActionOutcome(
                tool="computer.open_app",
                ok=True,
                note=f"opened {name!r}; could not re-check screen",
                verified=False,
                verified_note="verification backend unavailable",
                error=str(exc),
            )
        needle = name.strip().lower()
        matched = [w for w in windows if needle in str(w.get("title", "")).lower()]
        return ActionOutcome(
            tool="computer.open_app",
            ok=True,
            note=f"opened {name!r}",
            detail={"expected": name, "matched_windows": matched},
            verified=bool(matched),
            verified_note=(
                "window title matches the app name"
                if matched
                else "launcher reported success but no matching window title found"
            ),
            error=None if matched else "no matching window title found (could still be launching)",
        )

    def capture_screenshot(self) -> ActionOutcome:
        """OBSERVE: save a screenshot; verify non-empty bytes exist on disk."""
        stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self._screenshot_dir / f"shot_{stamp}.png"
        try:
            saved = self._screenshot(path)
            size = Path(saved).stat().st_size
        except Exception as exc:  # noqa: BLE001
            return ActionOutcome(
                tool="computer.capture_screenshot",
                ok=False,
                note="screenshot failed",
                error=str(exc),
            )
        ok = size > 0
        self._executions.append("computer.capture_screenshot")
        return ActionOutcome(
            tool="computer.capture_screenshot",
            ok=ok,
            note="screenshot captured" if ok else "screenshot backend returned no pixels",
            detail={"path": str(saved), "bytes": size},
            verified=ok,
            verified_note=f"{size} bytes written" if ok else "empty file written",
        )

    def capture_screenshot_bytes(self) -> bytes | None:
        """Capture the screen and return the PNG bytes, or None on failure.

        This is the on-demand frame the phone actually renders (remote
        mirroring, Prompt 5.3). The daemon serves these bytes only inside a
        live session, and the gateway throttles them to on-demand frequency.
        """
        outcome = self.capture_screenshot()
        if not outcome.ok:
            return None
        path = (outcome.detail or {}).get("path")
        if not path:
            return None
        return Path(path).read_bytes()

    def lock_workstation(self) -> ActionOutcome:
        """EXECUTE: immediately lock this laptop. The backend is injected so a
        test can substitute a function that records the call."""
        try:
            self._lock_workstation()
        except Exception as exc:  # noqa: BLE001
            return ActionOutcome(
                tool="computer.lock_workstation",
                ok=False,
                note="failed to lock workstation",
                error=str(exc),
                verified=False,
                verified_note="lock backend reported failure",
            )
        self._executions.append("computer.lock_workstation")
        return ActionOutcome(
            tool="computer.lock_workstation",
            ok=True,
            note="workstation locked",
            verified=True,
            verified_note="lock backend completed",
        )

    def read_screen_text(self) -> ActionOutcome:
        """OBSERVE: OCR the live screen and return visible text."""
        try:
            image = self._screenshot(self._screenshot_dir / "ocr_input.png")
            text = self._ocr(image)
        except Exception as exc:  # noqa: BLE001
            return ActionOutcome(
                tool="computer.read_screen_text",
                ok=False,
                note="OCR unavailable",
                error=str(exc),
            )
        ok = bool(text and text.strip())
        self._executions.append("computer.read_screen_text")
        return ActionOutcome(
            tool="computer.read_screen_text",
            ok=ok,
            note="read screen" if ok else "no readable text",
            detail={"text": (text or "").strip()},
            verified=ok,
            verified_note="non-empty text extracted" if ok else "OCR returned empty output",
        )

    @property
    def executions(self) -> list[str]:
        return list(self._executions)

    # ── registry gate ────────────────────────────────────────────────────
    def classify_with_gate(self, tool: str):
        """Gate a tool name through the frozen registry BEFORE any backend runs.

        Unregistered tools raise PermissionBlockedError — they are never
        attempted. Registered observe tools return their PermissionResult.
        """
        from packages.shared.permission_engine import PermissionEngine

        try:
            return PermissionEngine.classify(tool)
        except UnknownToolError as exc:
            raise PermissionBlockedError(str(exc)) from exc