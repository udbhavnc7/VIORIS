"""
Computer agent — window listing and launch (Phase 3, Prompt 3.1).

All OS interaction is isolated here and injected into the agent so tests run
without a live desktop. Defaults target Windows (the laptop) via PowerShell;
on other platforms they degrade to a named-tool best effort and report a
structured failure rather than silently doing nothing.

Windows paths stay explicit: `system.open_app` (Phase 1) launches by name;
this module only lists what is actually on screen and starts a named app.
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


class WindowLookupError(Exception):
    """A window-scan backend could not run."""


def default_list_windows() -> list[dict]:
    """List open windows with their titles and owning process.

    Uses PowerShell invoking the Win32 EnumWindows APIs through Add-Type.
    """
    if sys.platform != "win32":
        raise WindowLookupError(
            f"window listing not implemented for {sys.platform}; inject a backend"
        )
    script = r"""
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinEnum {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    public static object[] List() {
        var list = new List<object>();
        EnumWindows((hWnd, l) => {
            if (IsWindowVisible(hWnd)) {
                var sb = new System.Text.StringBuilder(256);
                GetWindowText(hWnd, sb, 256);
                if (sb.Length > 0) list.Add(new { handle = hWnd.ToInt64(), title = sb.ToString() });
            }
            return true;
        }, IntPtr.Zero);
        return list.ToArray();
    }
}
"@
[WinEnum]::List() | ConvertTo-Json -Compress
"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if out.returncode != 0:
        raise WindowLookupError(f"enumeration backend failed: {out.stderr.strip()[:200]}")
    import json

    parsed = json.loads(out.stdout)
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [dict(w) for w in parsed]


def default_open_app(daemon: str) -> None:
    """Launch a desktop application by name on this laptop."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["cmd", "/c", "start", "", daemon],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", daemon],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["xdg-open", daemon],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as exc:  # noqa: BLE001
        raise WindowLookupError(f"failed to launch '{daemon}': {exc}") from exc