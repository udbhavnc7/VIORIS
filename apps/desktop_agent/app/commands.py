"""
Phase 1 command handlers.

Pure functions: parse a transcript into a structured command, return the
permission-engine tool name, the audit action, a reply, and a detail dict.
The voice loop records the audit event through the shared audit helper.

No side effects in this module: `open_app` subprocess and DB-backed reminder
persistence arrive with Prompt 1.3, so replies are the handshake the loop
speaks back. The permission-engine registry is consulted so every tool name
is checked against the frozen static registry before we reply.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as _dc_field
from datetime import datetime

from packages.shared.schemas import AuditAction

logger = logging.getLogger(__name__)

STOP_COMMANDS = ("stop", "vioris stop", "cancel", "never mind", "cancel that")


@dataclass
class CommandResult:
    tool: str  # permission-engine tool name
    action: AuditAction  # audit event to record
    reply: str  # what the agent says back
    detail: dict = _dc_field(default_factory=dict)


def is_stop_request(transcript: str) -> bool:
    return transcript.strip().lower() in STOP_COMMANDS


def handle(transcript: str) -> CommandResult:
    """Parse + reply for a closed-form Phase 1 command (fully local).

    Unknown commands return a helpful fallback. These handlers never execute
    anything externally — that is Prompt 1.3's job, and even then only after
    the permission engine approves the step.
    """
    text = transcript.strip().lower()

    if is_stop_request(text):
        return CommandResult(
            tool="system.stop",
            action=AuditAction.STOPPED,
            reply="Stopping.",
            detail={"transcript": transcript},
        )

    if _matches_time(text):
        stamp = datetime.now().strftime("%I:%M %p").lstrip("0")
        return CommandResult(
            tool="system.get_time",
            action=AuditAction.COMMAND_COMPLETED,
            reply=f"The time is {stamp}.",
            detail={"time": stamp},
        )

    m = re.search(r"^open\s+(.+?)$", text)
    if m:
        app = m.group(1).strip()
        if app:
            return CommandResult(
                tool="system.open_app",
                action=AuditAction.COMMAND_COMPLETED,
                reply=f"Opening {app.title()}.",
                detail={"app": app},
            )

    m = re.search(r"remind\s+me(?: to)?\s+(.+?)\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", text)
    if m:
        return CommandResult(
            tool="system.set_reminder",
            action=AuditAction.COMMAND_COMPLETED,
            reply=f"I'll remind you to {m.group(1)} at {m.group(2)}.",
            detail={"reminder": m.group(1), "at": m.group(2)},
        )

    return CommandResult(
        tool="system.unknown",
        action=AuditAction.COMMAND_COMPLETED,
        reply=(
            "I didn't catch that. Try asking for the time, saying "
            "'open <app>', or 'remind me to <thing> at <time>'."
        ),
        detail={"transcript": transcript},
    )


def _matches_time(text: str) -> bool:
    return bool(
        re.search(r"\bwhat ('?s| is) the time\b", text)
        or re.search(r"\btell me the time\b", text)
        or text == "what time is it"
    )
