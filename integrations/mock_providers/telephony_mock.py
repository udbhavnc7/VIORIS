"""
Scripted mock Telephony transport for tests (Phase 6, Calling).

Implements the TelephonyTransport protocol with a canned contact book and
injectable failure, so connector tests never touch a real dialer. Mirrors the
real transport's v1 PROHIBITION: `open_dialer` only produces a handoff id — it
never places an autonomous call.
"""

from __future__ import annotations

from integrations.base import ConnectorError
from integrations.telephony.connector import ContactInfo

_AMBIGUOUS = {"alex"}
_CONTACTS = {
    "mum": ContactInfo(name="Mum", phone="+447700900001"),
    "dan": ContactInfo(name="Dan", phone="+447700900002"),
    "jane": ContactInfo(name="Jane", phone="+447700900004"),
}


class MockTelephonyTransport:
    """A native-dialer-shaped provider with a canned contact book + failure."""

    def __init__(self) -> None:
        self.name = "telephony"
        self.calls: list[str] = []
        self.handoffs: list[dict] = []
        self.fail_next: str | None = None

    def resolve_contact(self, recipient: str) -> ContactInfo:
        self.calls.append(f"resolve:{recipient}")
        key = recipient.strip().lower()
        if key in _AMBIGUOUS:
            raise ConnectorError(
                f"ambiguous recipient '{recipient}'; clarify which contact you mean"
            )
        contact = _CONTACTS.get(key)
        if contact:
            return contact
        # Unknown: surface the typed number as the canonical identity.
        return ContactInfo(name=recipient.strip(), phone=recipient.strip())

    def open_dialer(self, phone: str, script: str) -> str:
        self.calls.append(f"open_dialer:{phone}")
        if self.fail_next == "rate_limit":
            self.fail_next = None
            raise ConnectorError("mock rate limit on dialer handoff")
        handoff_id = f"handoff_{len(self.handoffs) + 1}"
        self.handoffs.append({"handoff_id": handoff_id, "phone": phone, "script": script})
        return handoff_id
