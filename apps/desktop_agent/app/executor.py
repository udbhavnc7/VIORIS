"""
Phase 1 command executor.

Turns a parsed CommandResult into a local action, always gated by the
permission engine:

  - classify the tool through the FROZEN static registry (never the LLM)
  - if confirmation_required -> the step is DEFERRED, not run
  - unregistered tools never fire

Phase 1 has no execute/critical tools (docs/03): open/get_time/stop are here
locally; anything that would require approval raises PermissionBlockedError and
the caller records it as waiting_approval instead of running it.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime

from packages.shared.permission_engine import PermissionEngine, UnknownToolError
from packages.shared.schemas import TaskStatus

logger = logging.getLogger(__name__)


class PermissionBlockedError(Exception):
    """The permission engine says this step needs approval; it was NOT run."""


class Executor:
    def __init__(self, open_app_fn=None) -> None:
        self.open_app_fn = open_app_fn or default_open_app
        self.executions: list[str] = []

    def execute(self, command_result) -> tuple[str, dict]:
        """Gate then run one command. Returns (status, result).

        Raises PermissionBlockedError when classification requires approval;
        the caller should record the step as waiting_approval, never run it.
        """
        tool = command_result.tool
        if tool in ("system.unknown", "unknown", "system.none"):
            return (TaskStatus.COMPLETED.value, {"note": "command not understood"})

        try:
            permission = PermissionEngine.classify(tool)
        except UnknownToolError:
            raise PermissionBlockedError(f"Tool '{tool}' is not registered - blocked.")

        if permission.confirmation_required:
            raise PermissionBlockedError(f"Tool '{tool}' requires approval - deferred.")

        if tool == "system.get_time":
            result = {"time": datetime.now().strftime("%I:%M %p")}
        elif tool == "system.open_app":
            app = command_result.detail.get("app")
            if not app:
                raise PermissionBlockedError("open_app without an app target")
            self.open_app_fn(app)
            result = {"app": app, "launched": True}
        elif tool == "system.set_reminder":
            result = {
                "reminder": command_result.detail.get("reminder"),
                "at": command_result.detail.get("at"),
                "status": "staged",
            }
        elif tool == "system.stop":
            result = {"status": "stopping"}
        else:
            raise PermissionBlockedError(f"No Phase 1 executor for tool '{tool}'.")

        self.executions.append(tool)
        return (TaskStatus.COMPLETED.value, result)


def default_open_app(app: str) -> None:
    """Launch a desktop app by name on this laptop (Phase 1, local only)."""
    try:
        subprocess.Popen(
            _launcher(app),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("launched app: %s", app)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to open app '%s': %s", app, exc)


def _launcher(app: str) -> list[str]:
    """Return the argv needed to launch `app` on this OS."""
    if sys.platform == "darwin":
        return ["open", app]
    if sys.platform == "win32":
        return ["cmd", "/c", "start", "", app]
    return ["xdg-open", app]
