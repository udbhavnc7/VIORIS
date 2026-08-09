"""
Phase 1 shared command runner.

One code path for handling a single transcript, used by both the voice loop
and the web console so they can never diverge:

  parse (commands.handle) -> permission gate (Executor) -> persist (TaskStore)

Returns a plain dict so callers (WebSocket/HTTP/loop) can render it however
they like. Everything is local; nothing here talks to any account.
"""

from __future__ import annotations

from .commands import handle
from .executor import PermissionBlockedError
from packages.shared.schemas import TaskStatus


def run_command(transcript: str, executor, store) -> dict:
    """Parse, gate, execute, and record one command. Never raises on the gate."""
    result = handle(transcript)
    reply = result.reply
    status: str = TaskStatus.COMPLETED.value
    outcome: dict = {}
    blocked: str | None = None

    try:
        status, outcome = executor.execute(result)
    except PermissionBlockedError as exc:
        blocked = str(exc)
        status = TaskStatus.WAITING_APPROVAL.value
        if store is not None:
            store.record_task(
                transcript,
                TaskStatus.WAITING_APPROVAL,
                "blocked",
                result.tool,
                {"why": blocked},
            )

    if store is not None and blocked is None:
        task_id = store.record_task(transcript, TaskStatus(status), "observe", result.tool, outcome)
        store.append_audit("system", "executed", {"task_id": task_id}, task_id)
    elif store is not None:
        store.append_audit(
            "system", "requested_approval", {"tool": result.tool, "why": blocked}, None
        )

    return {
        "tool": result.tool,
        "reply": reply,
        "status": status,
        "outcome": outcome,
        "blocked": blocked,
    }
