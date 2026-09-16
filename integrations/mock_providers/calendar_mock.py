"""
Scripted mock Calendar transport for tests (Phase 6).

Implements the CalendarTransport protocol with canned upcoming events and
injectable failures, so connector tests never touch the network. Records the
scope requested so tests can assert read-only discipline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from integrations.base import ConnectorError, ExpiredSessionError, RateLimitError


class MockCalendarTransport:
    """A Google-Calendar-shaped provider with canned events + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self, events: list[dict] | None = None) -> None:
        self.name = "calendar"
        self.events = events or _DEFAULT_EVENTS
        self.calls: list[str] = []
        self.revoked: bool = False
        self._auth_scope: str = ""
        self.fail_next: str | None = None
        self._sessions: dict[str, dict] = {}

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str:
        self._auth_scope = scope
        return f"https://calendar.mock/authorize?state={state}&scope={scope}"

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        self.last_requested_scope = self._auth_scope or "(none)"
        access = f"at_{code}"
        refresh = f"rt_{code}"
        self._sessions[refresh] = {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
            "account_id": "mock-user@gmail.com",
            "scope": self._auth_scope or "",
        }
        return dict(self._sessions[refresh])

    def refresh_access_token(self, refresh_token: str) -> dict:
        self.calls.append("refresh")
        sess = self._sessions.get(refresh_token)
        if sess is None:
            raise ConnectorError("unknown refresh token")
        sess["access_token"] = f"at2_{refresh_token}"
        return dict(sess)

    def revoke(self, access_token: str) -> None:
        self.calls.append("revoke")
        self.revoked = True

    def list_upcoming(self, access_token: str, *, max_results: int = 10, time_min: str = "") -> dict:
        self.calls.append(f"list_upcoming:{access_token}")
        self._maybe_fail()
        items = self.events[:max_results]
        return {
            "items": items,
            "nextPageToken": "p1" if len(self.events) > max_results else None,
        }

    def _maybe_fail(self) -> None:
        if self.fail_next == self.FAIL_RATE_LIMIT:
            self.fail_next = None
            raise RateLimitError("mock 429")
        if self.fail_next == self.FAIL_EXPIRED:
            self.fail_next = None
            raise ExpiredSessionError("mock 401 access token invalid")


now = datetime.now(UTC)
_DEFAULT_EVENTS = [
    {
        "id": "e1",
        "summary": "Dentist appointment",
        "start": {"dateTime": (now + timedelta(hours=1)).isoformat()},
        "attendees": [{"email": "clinic@example.com", "displayName": "Smile Clinic"}],
        "location": "123 High St",
    },
    {
        "id": "e2",
        "summary": "URGENT: project deadline review",
        "start": {"dateTime": (now + timedelta(days=1)).isoformat()},
        "attendees": [{"email": "boss@example.com", "displayName": "Boss"}],
    },
    {
        "id": "e3",
        "summary": "Team standup",
        "start": {"dateTime": (now + timedelta(days=2)).isoformat()},
        "attendees": [{"email": "team@example.com", "displayName": "Team"}],
    },
]