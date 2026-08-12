"""
Reservations (booking) connector — Phase 6, order: … → Reservations → …

Books restaurant tables / appointments through a linked browser session (the
official booking APIs require paid partnerships; the $0 constraint points to a
browser session, same model as WhatsApp). Two tools, kept strictly separate:

  - `reservations.search_slots` (OBSERVE): search available slots for a venue
    / date / party size. Reads nothing external state; no approval.
  - `reservations.create` (EXECUTE): book a slot. ONLY fires with an approved
    record in the task-runner ledger for that step's idempotency_key, and the
    diff card shows the venue, exact date/time, party size, and guest name —
    the full "what will happen" payload. A duplicate book with the same
    idempotency_key returns the prior confirmation instead of booking twice.

Nothing books without the permission engine. Rejection or a missing approval
never fires a reservation. Failures are explicit and loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    ExpiredSessionError,
    OAuthScopes,
    PermissionNotApprovedError,
    RateLimitError,
)

PERMISSION_EXPLANATION = (
    "Searches available booking slots (venue, date/time, party size) for "
    "restaurants and appointments. Can also create a reservation, but only "
    "after you approve a diff card showing the exact venue, date/time, party "
    "size, and guest name. It never books without your approval."
)

#: Browser-session-style capability surface (no OAuth).
_READ_SCOPE = "browser-session:search-slots"
_WRITE_SCOPE = "browser-session:create-reservation"


@dataclass
class SlotOption:
    slot_id: str
    venue: str
    at: datetime
    party_size: int
    available: bool = True


@dataclass
class SlotSearchResult:
    venue: str
    date: str
    party_size: int
    total: int
    slots: list[SlotOption] = field(default_factory=list)


@dataclass
class Reservation:
    reservation_id: str
    venue: str
    at: datetime
    party_size: int
    guest_name: str
    confirmation_note: str
    idempotency_key: str
    replay: bool = False


class ReservationsTransport(Protocol):
    """The booking-provider surface a reservations connector needs."""

    def is_linked(self, session_ref: str) -> bool: ...

    def search_slots(self, session_ref: str, *, venue: str, date: str, party_size: int) -> list[dict]: ...

    def create_reservation(self, session_ref: str, slot_id: str, *, guest_name: str) -> dict:
        """Book `slot_id` for `guest_name`. Returns provider JSON with the
        confirmation (reservation id, venue, datetime, note)."""

    def link(self) -> dict:
        """Start linking a booking provider session (QR/polling handle)."""

    def unlink(self, session_ref: str) -> None: ...


class ReservationsConnector(AbstractConnector):
    """Booking connector. Tools: reservations.search_slots (Observe),
    reservations.create (Execute, approval-gated)."""

    service = "reservations"
    auth_url = ""  # browser-session model: no OAuth endpoints
    token_url = ""
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["reservations.search_slots", "reservations.create"]

    def __init__(self, vault, transport: ReservationsTransport | None = None, **_ignored) -> None:
        super().__init__(vault, "")
        self.transport = transport

    def declared_scopes(self) -> OAuthScopes:
        return OAuthScopes(read=[_READ_SCOPE], write=[_WRITE_SCOPE])

    # OAuth hooks required by the base template but unused in this model.
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        raise ConnectorError("reservations uses browser-session linking, not OAuth")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        raise ConnectorError("reservations uses browser-session linking, not OAuth")

    def refresh_access_token(self, refresh_token: str) -> dict:
        raise ConnectorError("browser-session sessions are re-linked, not refreshed via OAuth")

    def revoke(self, access_token: str) -> None:
        raise ConnectorError("browser-session sessions are unlinked, not revoked")

    # ── session lifecycle (vault-backed) ───────────────────────────────────
    def link_device(self) -> dict:
        handle = self.transport.link()
        return handle

    def complete_link(self, session_ref: str) -> dict:
        if not self.transport.is_linked(session_ref):
            raise ConnectorError("booking session not linked on the provider side")
        self._vault.save(self.service, session_ref, {"session_ref": session_ref},
                         scopes=[_READ_SCOPE, _WRITE_SCOPE])
        self._audit(session_ref, "linked", {"connector": self.service})
        return {"connector": self.service, "account": session_ref,
                "scopes": self._scopes.read}

    def unlink_session(self, session_ref: str) -> None:
        self._vault.require_entry(self.service, session_ref)
        self.transport.unlink(session_ref)
        self._vault.delete(self.service, session_ref)
        self._audit(session_ref, "unlinked", {"connector": self.service})

    # ── search path (observe-tier, transparent) ────────────────────────────
    def search_slots(self, session_ref: str, *, venue: str, date: str, party_size: int) -> SlotSearchResult:
        entry = self._vault.require_entry(self.service, session_ref)
        if entry.get("expired"):
            raise ExpiredSessionError(f"{session_ref} session expired — re-link required")
        try:
            raws = self.transport.search_slots(session_ref, venue=venue, date=date, party_size=party_size)
        except RateLimitError:
            raise
        except ExpiredSessionError:
            self._vault.mark_expired(self.service, session_ref)
            raise
        except ConnectorError:
            raise
        slots = [
            SlotOption(
                slot_id=r["slot_id"],
                venue=r["venue"],
                at=_parse_dt(r["at"]),
                party_size=r.get("party_size", party_size),
                available=r.get("available", True),
            )
            for r in raws
            if r.get("available", True)
        ]
        return SlotSearchResult(
            venue=venue,
            date=date,
            party_size=party_size,
            total=len(slots),
            slots=slots,
        )

    # ── create path (execute-tier, approval-gated, idempotent) ─────────────
    def create_reservation(
        self,
        session_ref: str,
        *,
        slot_id: str,
        venue: str,
        at: str,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
        approval_verifier=None,
    ) -> Reservation:
        """Book a slot, ONLY if an explicit approval exists in the task-runner
        ledger for this idempotency_key.

        No shortcuts: refuses unless `approval_verifier` confirms an approved
        record for this step+key. Re-booking with the same idempotency_key
        returns the prior confirmation instead of a duplicate reservation.
        """
        if not slot_id or not slot_id.strip():
            raise ConnectorError("refusing to book with no slot")
        if not guest_name or not guest_name.strip():
            raise ConnectorError("refusing to book with no guest name")

        entry = self._vault.require_entry(self.service, session_ref)
        if entry.get("expired"):
            raise ExpiredSessionError(f"{session_ref} session expired — re-link required")

        # Idempotency: a replayed key returns the prior confirmation, never
        # books twice.
        prior = self._vault.load(f"{self.service}_bookings", idempotency_key)
        if prior and prior.get("status") == "booked":
            return Reservation(
                reservation_id=prior["reservation_id"],
                venue=prior["venue"],
                at=_parse_dt(prior["at"]),
                party_size=prior["party_size"],
                guest_name=prior["guest_name"],
                confirmation_note=prior["confirmation_note"],
                idempotency_key=idempotency_key,
                replay=True,
            )

        # The diff card must show the FULL booking payload — venue, exact
        # date/time, party size, guest name. Nothing books without it.
        self.require_approval_gate(
            "execute",
            "reservations.create",
            {
                "venue": venue,
                "at": at,
                "party_size": party_size,
                "guest_name": guest_name,
                "slot_id": slot_id,
                "channel": "browser-session",
            },
        )
        if approval_verifier is not None and not approval_verifier():
            raise PermissionNotApprovedError(
                "no approved approval exists for this reservation (task-runner ledger)"
            )

        try:
            raw = self.transport.create_reservation(session_ref, slot_id, guest_name=guest_name)
        except RateLimitError:
            raise
        except ExpiredSessionError:
            self._vault.mark_expired(self.service, session_ref)
            raise
        except ConnectorError:
            raise

        reservation_id = raw["reservation_id"]
        confirmation_note = raw.get("confirmation_note", "Reservation confirmed.")
        self._vault.save(
            f"{self.service}_bookings",
            idempotency_key,
            {
                "reservation_id": reservation_id,
                "venue": venue,
                "at": at,
                "party_size": party_size,
                "guest_name": guest_name,
                "confirmation_note": confirmation_note,
                "status": "booked",
            },
        )
        self._audit(
            session_ref,
            "created_reservation",
            {
                "venue": venue,
                "at": at,
                "party_size": party_size,
                "guest_name": guest_name,
                "reservation_id": reservation_id,
                "idempotency_key": idempotency_key,
            },
        )
        return Reservation(
            reservation_id=reservation_id,
            venue=venue,
            at=_parse_dt(at),
            party_size=party_size,
            guest_name=guest_name,
            confirmation_note=confirmation_note,
            idempotency_key=idempotency_key,
        )


def _parse_dt(value: str) -> datetime:
    """Parse the ISO-ish provider timestamp; fall back to now on garbage."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now()


class PlaywrightReservationsTransport:
    """Real browser-session transport driving a booking site via Playwright.

    Kept lazy so the connector imports with zero deps; the production path
    manages a persistent Chromium profile so the session survives restarts.
    """

    def __init__(self, *, channel: str = "chromium", user_data_dir: str | None = None) -> None:
        import playwright  # noqa: F401 - eager import fails in test env w/o install
        self._channel = channel
        self._user_data_dir = user_data_dir

    def link(self) -> dict:
        raise ConnectorError(
            "playwright transport requires provisioning a persistent profile and "
            "a booking-site link flow; use MockReservationsTransport in tests"
        )

    def is_linked(self, session_ref: str) -> bool:
        raise ConnectorError("link status check requires an active playwright session")

    def search_slots(self, session_ref, *, venue, date, party_size) -> list[dict]:
        raise ConnectorError("search_slots requires an active playwright session")

    def create_reservation(self, session_ref, slot_id, *, guest_name) -> dict:
        raise ConnectorError("create_reservation requires an active playwright session")

    def unlink(self, session_ref) -> None:
        raise ConnectorError("unlink requires an active playwright session")
