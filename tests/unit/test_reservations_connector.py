"""
Unit tests for the Phase 6 Reservations (booking) connector.

Covers: browser-session link/unlink lifecycle, Observe-tier slot search (only
available slots returned), Execute-tier booking with approval-ledger gating,
idempotency replay, and loud empty/expired handling. A booking never fires
without an approved record.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    ConnectorError,
    ExpiredSessionError,
    PermissionNotApprovedError,
    RateLimitError,
)
from integrations.mock_providers.reservations_mock import MockReservationsTransport
from integrations.reservations.connector import (
    Reservation,
    ReservationsConnector,
    SlotSearchResult,
)
from integrations.token_vault import TokenVault

from packages.shared.permission_engine import PermissionEngine
from packages.shared.schemas import RiskTier, ToolRegistration


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "res.db", key=Fernet.generate_key())


@pytest.fixture()
def transport() -> MockReservationsTransport:
    return MockReservationsTransport()


@pytest.fixture()
def rc(vault: TokenVault, transport: MockReservationsTransport) -> ReservationsConnector:
    return ReservationsConnector(vault, transport=transport)


@pytest.fixture(autouse=True)
def _reset_registry():
    PermissionEngine.reset()
    yield
    PermissionEngine.reset()


def _register() -> None:
    PermissionEngine.register(
        ToolRegistration(
            tool_name="reservations.search_slots",
            tier=RiskTier.OBSERVE,
            confirmation_required=False,
            description="search",
        )
    )
    PermissionEngine.register(
        ToolRegistration(
            tool_name="reservations.create",
            tier=RiskTier.EXECUTE,
            confirmation_required=True,
            description="book",
            diff_card_fields=["venue", "at", "party_size", "guest_name", "slot_id", "channel"],
        )
    )


def _linked(rc: ReservationsConnector, transport: MockReservationsTransport, ref: str = "sess-r") -> str:
    transport.complete_scan(ref)
    rc.complete_link(ref)
    return ref


def test_scopes_and_tools_are_separated(rc: ReservationsConnector) -> None:
    scopes = rc.declared_scopes()
    assert scopes.read == ["browser-session:search-slots"]
    assert scopes.write == ["browser-session:create-reservation"]
    assert set(rc.tools) == {"reservations.search_slots", "reservations.create"}


def test_link_unlink_lifecycle(rc: ReservationsConnector, transport: MockReservationsTransport) -> None:
    ref = _linked(rc, transport)
    assert rc._vault.require_entry("reservations", ref) is not None
    rc.unlink_session(ref)
    assert rc._vault.load("reservations", ref) is None
    with pytest.raises(ConnectionMissingError):
        rc.unlink_session(ref)


def test_search_returns_only_available_slots(
    rc: ReservationsConnector, transport: MockReservationsTransport
) -> None:
    _register()
    _linked(rc, transport)
    result = rc.search_slots("sess-r", venue="Trattoria Roma", date="2026-09-01", party_size=2)
    assert isinstance(result, SlotSearchResult)
    assert result.total == 2  # slot-2 is provider-marked unavailable -> dropped
    assert all(s.available for s in result.slots)


def test_search_expired_session_marks_expired(
    rc: ReservationsConnector, transport: MockReservationsTransport
) -> None:
    _register()
    _linked(rc, transport)
    transport.fail_next = transport.FAIL_EXPIRED
    with pytest.raises(ExpiredSessionError):
        rc.search_slots("sess-r", venue="V", date="", party_size=2)
    assert rc._vault.load("reservations", "sess-r")["expired"] is True


def test_search_rate_limit_propagates_loudly(
    rc: ReservationsConnector, transport: MockReservationsTransport
) -> None:
    _register()
    _linked(rc, transport)
    transport.fail_next = transport.FAIL_RATE_LIMIT
    with pytest.raises(RateLimitError):
        rc.search_slots("sess-r", venue="V", date="", party_size=2)


def test_search_unlinked_refuses_early(rc: ReservationsConnector) -> None:
    _register()
    with pytest.raises(ConnectionMissingError):
        rc.search_slots("never-linked", venue="V", date="", party_size=2)


def test_create_requires_approved_ledger(
    rc: ReservationsConnector, transport: MockReservationsTransport
) -> None:
    _register()
    _linked(rc, transport)
    with pytest.raises(PermissionNotApprovedError):
        rc.create_reservation(
            "sess-r",
            slot_id="slot-1",
            venue="Trattoria Roma",
            at="2026-09-01T19:00:00",
            party_size=2,
            guest_name="Me",
            idempotency_key="res-1",
            approval_verifier=lambda: False,
        )
    assert transport.bookings == []  # nothing booked without approval


def test_create_fires_with_approved_ledger(
    rc: ReservationsConnector, transport: MockReservationsTransport
) -> None:
    _register()
    _linked(rc, transport)
    res = rc.create_reservation(
        "sess-r",
        slot_id="slot-1",
        venue="Trattoria Roma",
        at="2026-09-01T19:00:00",
        party_size=2,
        guest_name="Me",
        idempotency_key="res-2",
        approval_verifier=lambda: True,
    )
    assert isinstance(res, Reservation)
    assert res.replay is False
    assert res.confirmation_note == "Confirmed for Me."
    assert res.guest_name == "Me"
    assert transport.bookings[0]["slot_id"] == "slot-1"


def test_create_idempotency_replays_prior_confirmation(
    rc: ReservationsConnector, transport: MockReservationsTransport
) -> None:
    _register()
    _linked(rc, transport)
    first = rc.create_reservation(
        "sess-r",
        slot_id="slot-1",
        venue="Trattoria Roma",
        at="2026-09-01T19:00:00",
        party_size=2,
        guest_name="Me",
        idempotency_key="res-3",
        approval_verifier=lambda: True,
    )
    second = rc.create_reservation(
        "sess-r",
        slot_id="slot-1",
        venue="Trattoria Roma",
        at="2026-09-01T19:00:00",
        party_size=2,
        guest_name="Me",
        idempotency_key="res-3",
        approval_verifier=lambda: True,
    )
    assert second.replay is True
    assert second.reservation_id == first.reservation_id
    assert len(transport.bookings) == 1  # duplicate retry never books twice


def test_create_refuses_empty_guest(rc: ReservationsConnector, transport: MockReservationsTransport) -> None:
    _register()
    _linked(rc, transport)
    with pytest.raises(ConnectorError, match="guest name"):
        rc.create_reservation(
            "sess-r",
            slot_id="slot-1",
            venue="V",
            at="2026-09-01T19:00:00",
            party_size=2,
            guest_name="  ",
            idempotency_key="res-4",
            approval_verifier=lambda: True,
        )


def test_create_unlinked_session_is_loud(rc: ReservationsConnector) -> None:
    _register()
    with pytest.raises(ConnectionMissingError):
        rc.create_reservation(
            "never-linked",
            slot_id="slot-1",
            venue="V",
            at="2026-09-01T19:00:00",
            party_size=2,
            guest_name="Me",
            idempotency_key="res-5",
            approval_verifier=lambda: True,
        )
