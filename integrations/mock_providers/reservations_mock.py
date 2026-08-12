"""
Scripted mock Reservations transport for tests (Phase 6, Reservations).

Implements the ReservationsTransport protocol with a canned venue/slot book and
injectable expired states, so connector tests never touch a real booking site.
Mirrors the real transport's PROHIBITIONS: search returns only available slots,
and create_reservation only produces a confirmation (it is never reversible
silently — the connector gates it at Execute tier before this is reached).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from integrations.base import ExpiredSessionError, RateLimitError


class MockReservationsTransport:
    """A booking-provider-shaped mock with a canned venue + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self) -> None:
        self.name = "reservations"
        self.calls: list[str] = []
        self.linked_sessions: set[str] = set()
        self.fail_next: str | None = None
        self.bookings: list[dict] = []
        self._now = datetime.now(timezone.utc)

    def link(self) -> dict:
        self.calls.append("link")
        return {"status": "awaiting_scan", "handle": "qr_booking"}

    def is_linked(self, session_ref: str) -> bool:
        self.calls.append(f"is_linked:{session_ref[:8]}")
        return session_ref in self.linked_sessions

    def complete_scan(self, session_ref: str) -> None:
        """Test helper: mark a scanned session as linked."""
        self.linked_sessions.add(session_ref)

    def search_slots(self, session_ref: str, *, venue: str, date: str, party_size: int) -> list[dict]:
        self.calls.append(f"search_slots:{venue}:{date}")
        if session_ref not in self.linked_sessions:
            raise ExpiredSessionError("session not linked (401)")
        self._maybe_fail()
        return [
            {
                "slot_id": "slot-1",
                "venue": venue or "Trattoria Roma",
                "at": (self._now + timedelta(hours=2)).isoformat(),
                "party_size": party_size,
                "available": True,
            },
            {
                "slot_id": "slot-2",
                "venue": venue or "Trattoria Roma",
                "at": (self._now + timedelta(hours=4)).isoformat(),
                "party_size": party_size,
                "available": False,  # provider says taken
            },
            {
                "slot_id": "slot-3",
                "venue": venue or "Trattoria Roma",
                "at": (self._now + timedelta(hours=6)).isoformat(),
                "party_size": party_size,
                "available": True,
            },
        ]

    def create_reservation(self, session_ref: str, slot_id: str, *, guest_name: str) -> dict:
        self.calls.append(f"create_reservation:{slot_id}")
        if session_ref not in self.linked_sessions:
            raise ExpiredSessionError("session not linked (401)")
        self._maybe_fail()
        booking_id = f"res_{len(self.bookings) + 1}"
        self.bookings.append(
            {"reservation_id": booking_id, "slot_id": slot_id, "guest_name": guest_name}
        )
        return {
            "reservation_id": booking_id,
            "confirmation_note": f"Confirmed for {guest_name}.",
        }

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
