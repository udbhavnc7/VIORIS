"""
Scripted mock WhatsApp browser session for tests (Phase 6, Prompt 6.2).

Implements the WhatsAppTransport protocol with canned conversations plus
injectable inaccessible/expired states, so connector tests never touch a real
browser. Mirrors the real transport's PROHIBITIONS: read is read-only, and any
message the view cannot surface is flagged rather than dropped. Send is
mockable too: `resolve_recipient` + `send_message` behave like a linked Web
session (ambiguous names raise, sends record a message id).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from integrations.base import ConnectorError, ExpiredSessionError, RateLimitError
from integrations.whatsapp.connector import RawMessage


class MockWhatsAppSession:
    """A browser-session-shaped provider with canned chats + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self, messages: list[RawMessage] | None = None) -> None:
        self.name = "whatsapp"
        self.messages = messages or _DEFAULT_MESSAGES
        self.calls: list[str] = []
        self.linked_sessions: set[str] = set()
        self.fail_next: str | None = None
        self.sent: list[dict] = []
        self._now = datetime.now(timezone.utc)

    # ── transport surface ─────────────────────────────────────────────────
    def link(self) -> dict:
        self.calls.append("link")
        return {"status": "awaiting_scan", "handle": "qr_123"}

    def is_linked(self, session_ref: str) -> bool:
        self.calls.append(f"is_linked:{session_ref[:8]}")
        return session_ref in self.linked_sessions

    def complete_scan(self, session_ref: str) -> None:
        """Test helper: mark a scanned session as linked."""
        self.linked_sessions.add(session_ref)

    def fetch_recent(self, session_ref: str, *, hours: int = 24, max_messages: int = 40) -> list[RawMessage]:
        self.calls.append(f"fetch_recent:{session_ref[:8]}")
        if session_ref not in self.linked_sessions:
            raise ExpiredSessionError("session not linked (401)")
        self._maybe_fail()
        window = self._now - timedelta(hours=hours)
        recent = [m for m in self.messages if (m.timestamp or window) >= window]
        return recent[:max_messages]

    def resolve_recipient(self, session_ref: str, recipient: str) -> str:
        self.calls.append(f"resolve:{recipient}")
        if session_ref not in self.linked_sessions:
            raise ExpiredSessionError("session not linked (401)")
        key = recipient.strip().lower()
        if key in _AMBIGUOUS:
            raise ConnectorError(
                f"ambiguous recipient '{recipient}'; clarify which contact you mean"
            )
        canonical = _CONTACTS.get(key)
        if canonical:
            return canonical
        # Unknown name: treat it as a phone-number-ish identity, surfaced as-is.
        return recipient.strip()

    def send_message(self, session_ref: str, recipient_identity: str, content: str) -> str:
        self.calls.append(f"send:{recipient_identity}")
        if session_ref not in self.linked_sessions:
            raise ExpiredSessionError("session not linked (401)")
        self._maybe_fail()
        message_id = f"sent_{len(self.sent) + 1}"
        self.sent.append(
            {"message_id": message_id, "recipient_identity": recipient_identity, "content": content}
        )
        return message_id

    def unlink(self, session_ref: str) -> None:
        self.calls.append("unlink")
        self.linked_sessions.discard(session_ref)

    # ── failure injection ─────────────────────────────────────────────────
    def _maybe_fail(self) -> None:
        if self.fail_next == self.FAIL_RATE_LIMIT:
            self.fail_next = None
            raise RateLimitError("mock 429")
        if self.fail_next == self.FAIL_EXPIRED:
            self.fail_next = None
            raise ExpiredSessionError("mock 401 session invalid")


_AMBIGUOUS = {"alex"}
_CONTACTS = {
    "mum": "Mum <+447700900001>",
    "dan (florist)": "Dan <+447700900002>",
    "dan": "Dan <+447700900002>",
    "student office": "Student Office <+447700900003>",
    "jane": "Jane <+447700900004>",
}


_NOW = datetime.now(timezone.utc)
_DEFAULT_MESSAGES = [
    RawMessage(
        message_id="w1",
        sender="Mum",
        text="How are you? Please send me those photos of the garden.",
        timestamp=_NOW - timedelta(hours=1),
    ),
    RawMessage(
        message_id="w2",
        sender="Dan (florist)",
        text="Asking about the bouquet delivery — can you confirm by today?",
        timestamp=_NOW - timedelta(hours=3),
    ),
    RawMessage(
        message_id="w3",
        sender="Student Office",
        text="requesting your student information for the enrolment form",
        timestamp=_NOW - timedelta(hours=6),
    ),
    RawMessage(
        message_id="w4",
        sender="Unknown",
        text="[ViewOnce photo — not readable through this connector]",
        kind="media",
        inaccessible_reason="view-once media; WhatsApp Web cannot surface for re-reading",
        timestamp=_NOW - timedelta(hours=8),
    ),
    RawMessage(
        message_id="w5",
        sender="Group: Team Lunch",
        text="Tomorrow at 1pm?",
        timestamp=_NOW - timedelta(hours=10),
    ),
]