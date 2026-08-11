"""
Unit tests for the Phase 6 Calendar connector.

Covers: OAuth lifecycle (connect / refresh / revoke / disconnect), encrypted
storage, read-only scope discipline, rate-limit + expired-session handling,
and the upcoming-events digest with urgency estimation.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    RateLimitError,
)
from integrations.calendar.connector import (
    CalendarConnector,
    CalendarDigest,
    _urgency,
    _attendee_names,
)
from integrations.mock_providers.calendar_mock import MockCalendarTransport
from integrations.token_vault import TokenVault


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "cal.db", key=Fernet.generate_key())


@pytest.fixture()
def mock() -> MockCalendarTransport:
    return MockCalendarTransport()


@pytest.fixture()
def cal(vault: TokenVault, mock: MockCalendarTransport) -> CalendarConnector:
    return CalendarConnector(vault, transport=mock)


# ── lifecycle ────────────────────────────────────────────────────────────────


def test_connect_stores_and_reports_read_only(cal: CalendarConnector) -> None:
    summary = cal.connect("the-code", "http://127.0.0.1:8447/authorized")
    assert summary["account"] == "mock-user@gmail.com"
    assert summary["scopes"] == ["https://www.googleapis.com/auth/calendar.readonly"]


def test_declared_scopes_are_read_only(cal: CalendarConnector) -> None:
    scopes = cal.declared_scopes()
    assert "calendar.readonly" in scopes.read[0]
    assert scopes.write == []  # READ-ONLY: never a write scope


def test_connect_missing_identity_fails(cal: CalendarConnector) -> None:
    with pytest.raises(ConnectionMissingError):
        cal.fetch_upcoming_digest("ghost@gmail.com")


# ── digest path ──────────────────────────────────────────────────────────────


def test_digest_returns_upcoming_events(cal: CalendarConnector) -> None:
    cal.connect("c1", "http://127.0.0.1:8447/authorized")
    digest = cal.fetch_upcoming_digest("mock-user@gmail.com", hours=72)
    assert isinstance(digest, CalendarDigest)
    assert digest.total == 3
    events = {e.title: e for e in digest.events}
    assert "Dentist appointment" in events
    urgent = events["URGENT: project deadline review"]
    assert urgent.urgency == "high"
    assert "Boss" in urgent.attendees
    dentist = events["Dentist appointment"]
    assert dentist.location == "123 High St"
    assert "Smile Clinic" in dentist.attendees


def test_rate_limit_propagates_loudly(cal: CalendarConnector, mock: MockCalendarTransport) -> None:
    cal.connect("c3", "http://127.0.0.1:8447/authorized")
    mock.fail_next = mock.FAIL_RATE_LIMIT
    with pytest.raises(RateLimitError):
        cal.fetch_upcoming_digest("mock-user@gmail.com")


def test_expired_session_refreshes_once_then_succeeds(
    cal: CalendarConnector, mock: MockCalendarTransport
) -> None:
    cal.connect("c4", "http://127.0.0.1:8447/authorized")
    mock.fail_next = mock.FAIL_EXPIRED
    digest = cal.fetch_upcoming_digest("mock-user@gmail.com")
    assert digest.total == 3
    assert "refresh" in mock.calls


def test_disconnect_revokes_and_deletes(cal: CalendarConnector, mock: MockCalendarTransport) -> None:
    cal.connect("c6", "http://127.0.0.1:8447/authorized")
    cal.revoke_and_disconnect("mock-user@gmail.com")
    assert mock.revoked is True
    assert cal._vault.load("calendar", "mock-user@gmail.com") is None


# ── heuristics ───────────────────────────────────────────────────────────────


def test_urgency_scores() -> None:
    now_iso = "2026-01-01T00:00:00+00:00"
    soon = {"summary": "Review", "start": {"dateTime": now_iso}}
    assert _urgency({"summary": "deadline today", "start": {}}) == "high"
    assert _urgency({"summary": "chore", "start": {}}) == "low"
    assert _urgency(soon) == "high"  # near in time
    assert _urgency({"summary": "standup", "start": {"dateTime": now_iso}}) == "high"


def test_attendee_names_excludes_the_user() -> None:
    raw = {"attendees": [
        {"displayName": "me"},
        {"displayName": "Boss"},
        {"email": "clinic@example.com"},
    ]}
    names = _attendee_names(raw)
    assert "Boss" in names and "clinic@example.com" in names
    assert "me" not in names