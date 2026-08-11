"""
Computer agent — remote control (Phase 5, Prompt 5.3).

Two capabilities, both wired so the agent itself is testable offline:

  1. RemoteSessionManager — an explicit, auto-expiring control session for a
     paired device. A session carries a device_id and a timeout; past the
     timeout it is expired and rejects every input. Expiry is checked at
     CALL time (and lazily swept), so a phone that stops talking can never
     control the laptop indefinitely.

  2. Input backends — keyboard and mouse injection. Defaults target Windows
     via PowerShell (user32 SendInput for keys, mouse_event for clicks and
     moves); anything else raises InputBackendError so the caller reports the
     failure instead of pretending.

Every input is gated inside this module by an ACTIVE session for the exact
device that started it — never by the LLM.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TTL_SECONDS = 300  # remote sessions auto-expire after 5 minutes
MAX_SESSION_TTL_SECONDS = 3600


class RemoteSessionError(Exception):
    """A session does not exist, belongs to another device, or has expired."""


class InputBackendError(Exception):
    """A keyboard/mouse backend could not run."""


class InputRefusedError(Exception):
    """An input was refused because no active session exists."""


@dataclass
class RemoteSession:
    session_id: str
    device_id: str
    started_at: float
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "expired": self.expired,
            "ttl_seconds": max(0, int(self.expires_at - time.time())),
        }


class RemoteSessionManager:
    """Create + verify auto-expiring remote sessions (thread-safe)."""

    def __init__(self, ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, RemoteSession] = {}

    def create(self, device_id: str, timeout_minutes: int = 5) -> RemoteSession:
        """Start a new session for a device on the laptop."""
        ttl = self._coerce_ttl(timeout_minutes)
        now = time.time()
        session = RemoteSession(
            session_id=f"rs_{uuid.uuid4().hex[:10]}",
            device_id=device_id,
            started_at=now,
            expires_at=now + ttl,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def _coerce_ttl(self, timeout_minutes: int) -> int:
        if timeout_minutes is None or timeout_minutes <= 0:
            return self.ttl_seconds
        return min(int(timeout_minutes * 60), MAX_SESSION_TTL_SECONDS)

    def require_active(self, session_id: str, device_id: str) -> RemoteSession:
        """Return the session iff it exists, belongs to `device_id`, and is
        not expired. Raises RemoteSessionError otherwise — the input never
        fires."""
        self._sweep()
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise RemoteSessionError("no such remote session")
        if session.device_id != device_id:
            raise RemoteSessionError("session belongs to another device")
        if session.expired:
            raise RemoteSessionError("remote session expired")
        return session

    def active_for_device(self, device_id: str) -> RemoteSession | None:
        """Latest still-valid session for a device, if any."""
        self._sweep()
        with self._lock:
            matches = [
                s for s in self._sessions.values() if s.device_id == device_id and not s.expired
            ]
        return max(matches, key=lambda s: s.expires_at) if matches else None

    def end(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def end_all_for_device(self, device_id: str) -> int:
        """End every live session a device holds (Stop Everything uses this)."""
        with self._lock:
            live = [
                sid for sid, s in self._sessions.items()
                if s.device_id == device_id and not s.expired
            ]
            for sid in live:
                self._sessions.pop(sid, None)
            return len(live)

    def _sweep(self) -> None:
        with self._lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if s.expired and time.time() - s.expires_at > 60
            ]
            for sid in expired:
                self._sessions.pop(sid, None)


# ── keyboard / mouse backends (injectable, Windows-first) ────────────────────


def default_keyboard(text: str) -> None:
    """Type a string into the focused window via user32 SendInput."""
    if sys.platform != "win32":
        raise InputBackendError(f"keyboard backend not implemented on {sys.platform}")
    if not text or not text.strip():
        raise InputBackendError("nothing to type")
    escaped = repr(text)  # PS string literal; safe single user-provided value
    script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Kbd {{
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] i, int cb);
    [StructLayout(LayoutKind.Sequential)] public struct INPUT {{ public uint type; public KEYBDINPUT ki; }}
    [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT {{
        public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo;
    }}
    public static void Type(string s) {{
        foreach (char c in s) {{
            short vk = (short)(c & 0xFF);
            var down = new INPUT(); var up = new INPUT();
            down.type = up.type = 1;
            down.ki = new KEYBDINPUT{{ wVk = (ushort)vk }};
            up.ki = new KEYBDINPUT{{ wVk = (ushort)vk, dwFlags = 2 }};
            INPUT[] b = new INPUT[2] {{ down, up }};
            SendInput(2, b, INPUT.SIZE());
        }}
    }}
    public static int SIZE() {{ return Marshal.SizeOf(typeof(INPUT)); }}
}}
"@
[Kbd]::Type({escaped})
"""
    _run_powershell(script)


def default_mouse(action: str, x: int | None = None, y: int | None = None) -> None:
    """Click (left/right) or move the cursor. Coordinates may be absolute."""
    if sys.platform != "win32":
        raise InputBackendError(f"mouse backend not implemented on {sys.platform}")
    if action not in ("left", "right", "move"):
        raise InputBackendError(f"unknown mouse action {action!r}")
    if action == "move" and (x is None or y is None):
        raise InputBackendError("move requires x and y")
    if action in ("left", "right") and x is not None and y is not None:
        click_x, click_y = int(x), int(y)
    else:
        click_x = click_y = -1  # user32 keeps current position for clicks

    script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Mse {{
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
    public static void Move(int x, int y) {{ SetCursorPos(x, y); }}
    public static void Click(bool right) {{
        uint f = right ? 0x0008u : 0x0002u;   // RIGHTDOWN / LEFTDOWN
        mouse_event(f, 0, 0, 0, UIntPtr.Zero);
        mouse_event(f + 0x0004u, 0, 0, 0, UIntPtr.Zero); // ...UP
    }}
}}
"@
[Mse]::Move({click_x}, {click_y})
if ("{action}" -ne "move") {{ [Mse]::Click([bool]("$('{action}' -eq 'right')".ToString())) }}
"""
    _run_powershell(script)


def _run_powershell(script: str) -> None:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise InputBackendError(f"input backend failed: {result.stderr.strip()[:200]}")


# ── agent-facing entry points (used by the daemon) ───────────────────────────


class RemoteControl:
    """Gate + execute input through an active session for a device."""

    def __init__(
        self,
        sessions: RemoteSessionManager | None = None,
        keyboard_fn: Callable[[str], None] | None = None,
        mouse_fn: Callable[[str, int | None, int | None], None] | None = None,
    ) -> None:
        self.sessions = sessions or RemoteSessionManager()
        self._keyboard = keyboard_fn or default_keyboard
        self._mouse = mouse_fn or default_mouse

    def send_input(self, session_id: str, device_id: str, action: str, text: str | None = None,
                   x: int | None = None, y: int | None = None) -> None:
        """Refuse unless an ACTIVE session belonging to this device exists."""
        session = self.sessions.require_active(session_id, device_id)  # raises on expiry
        if action == "type":
            self._keyboard(text or "")
        elif action in ("left", "right", "move"):
            self._mouse(action, x, y)
        else:
            raise InputBackendError(f"unknown input action {action!r}")
        logger.info("input %s delivered on session %s", action, session.session_id)