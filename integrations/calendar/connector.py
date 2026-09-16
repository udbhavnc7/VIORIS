"""
Calendar connector — READ-ONLY (Phase 6, order: Calendar → …).

Minimum-viable scope: `calendar.readonly`. It lists upcoming events and
produces a digest (title / start / attendee / urgency / asks). There are NO
write scopes; creating or modifying events is intentionally unregistered, so
any such path is blocked by the permission engine rather than silently allowed.

OAuth + token handling mirror the Gmail connector exactly: encrypted vault,
transparent single refresh, loud rate-limit / expired-session failures, and an
injected transport so tests use a scripted mock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    OAuthScopes,
    RateLimitError,
)

_DEFAULT_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
_DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_API = "https://www.googleapis.com/calendar/v3"

# Minimum-viable read scope. No write scope on a read-only connector.
SCOPE_READONLY = "https://www.googleapis.com/auth/calendar.readonly"

PERMISSION_EXPLANATION = (
    "Read only your calendars: upcoming event titles, times, and who is "
    "invited. It cannot create, edit, or delete anything in your calendars."
)


class CalendarTransport(Protocol):
    """The small HTTP surface a calendar connector needs (mockable)."""

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> dict: ...

    def refresh_access_token(self, refresh_token: str) -> dict: ...

    def revoke(self, access_token: str) -> None: ...

    def list_upcoming(
        self, access_token: str, *, max_results: int = 10, time_min: str = ""
    ) -> dict:
        """GET events.list → {'items': [...], 'nextPageToken': ...}."""


# ── digest shapes ────────────────────────────────────────────────────────────


@dataclass
class CalendarEvent:
    event_id: str
    title: str
    start_at: datetime
    attendees: list[str] = field(default_factory=list)
    urgency: str = "low"  # low | medium | high
    summary: str = ""
    location: str | None = None
    details: str | None = None


@dataclass
class CalendarDigest:
    account: str
    window_hours: int
    total: int
    truncated: bool
    events: list[CalendarEvent]


# Local, deterministic heuristics — no external model, no per-request cost.


def _urgency(entry: dict) -> str:
    """Estimate urgency from timing proximity + wording (demo-heuristic)."""
    title = (entry.get("summary") or "").lower()
    soon_hours: float | None = None
    start = entry.get("start", {}).get("dateTime")
    if start:
        try:
            soon_hours = (datetime.fromisoformat(start) - datetime.now(UTC)).total_seconds() / 3600
        except ValueError:
            soon_hours = None
    high = ("urgent", "asap", "deadline", "final", "exam", "due today", "important")
    near_high = soon_hours is not None and soon_hours <= 2
    if any(h in title for h in high):
        return "high"
    if near_high:
        return "high"
    if soon_hours is not None and soon_hours <= 12:
        return "medium"
    return "low"


def _summary(entry: dict) -> str:
    title = entry.get("summary") or "(untitled event)"
    loc = entry.get("location")
    if loc:
        return f"{title} — {loc}"
    return title


_USER_ME = ("me", "i", "myself", "my calendar")


def _attendee_names(entry: dict) -> list[str]:
    out: list[str] = []
    for a in entry.get("attendees", []) or []:
        name = a.get("displayName") or a.get("email", "")
        if name and name.lower() not in _USER_ME:
            out.append(name)
    return out[:5]


# ── real transport (httpx) ───────────────────────────────────────────────────


class HttpxCalendarTransport:
    """Production transport against Google's OAuth + Calendar REST APIs."""

    def __init__(self, client_id: str, client_secret: str,
                 auth_url: str = _DEFAULT_AUTH_URL,
                 token_url: str = _DEFAULT_TOKEN_URL) -> None:
        import httpx  # lazy: the connector imports without dependencies
        self._httpx = httpx
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_url = auth_url
        self.token_url = token_url

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str:
        return (
            f"{self.auth_url}?client_id={self.client_id}&redirect_uri={redirect_uri}"
            f"&response_type=code&scope={scope}&access_type=offline&prompt=consent&state={state}"
        )

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        resp = self._httpx.post(
            self.token_url,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("exchange_code: 429")
        resp.raise_for_status()
        return resp.json()

    def refresh_access_token(self, refresh_token: str) -> dict:
        resp = self._httpx.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            },
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("refresh_access_token: 429")
        if resp.status_code == 400:
            raise ConnectorError("expired: refresh token rejected")
        resp.raise_for_status()
        return resp.json()

    def revoke(self, access_token: str) -> None:
        self._httpx.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": access_token},
            timeout=20,
        )

    def list_upcoming(self, access_token: str, *, max_results: int = 10, time_min: str = "") -> dict:
        params = {
            "maxResults": str(max_results),
            "orderBy": "startTime",
            "singleEvents": "true",
            "timeMin": time_min or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        resp = self._httpx.get(
            f"{_CALENDAR_API}/calendars/primary/events",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("list_upcoming: 429")
        if resp.status_code in (401, 403):
            raise ConnectorError("expired: access token rejected")
        resp.raise_for_status()
        return resp.json()


# ── connector ────────────────────────────────────────────────────────────────


class CalendarConnector(AbstractConnector):
    """Read-only calendar integration. Tools: calendar.read_upcoming."""

    service = "calendar"
    auth_url = _DEFAULT_AUTH_URL
    token_url = _DEFAULT_TOKEN_URL
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["calendar.read_upcoming"]

    def __init__(self, vault, transport: CalendarTransport | None = None,
                 token_endpoint: str | None = None, **_ignored) -> None:
        super().__init__(vault, token_endpoint or self.token_url)
        self.transport = transport or HttpxCalendarTransport("", "")

    def declared_scopes(self) -> OAuthScopes:
        # READ-ONLY: write scopes intentionally empty. Never merge.
        return OAuthScopes(read=[SCOPE_READONLY], write=[])

    # OAuth lifecycle hooks (tokens stay in the vault)
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        return self.transport.authorize_uri(state, redirect_uri, " ".join(self._scopes.read))

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        return self.transport.exchange_code(code, redirect_uri)

    def refresh_access_token(self, refresh_token: str) -> dict:
        return self.transport.refresh_access_token(refresh_token)

    def revoke(self, access_token: str) -> None:
        self.transport.revoke(access_token)

    # ── read path (observe-tier, transparent) ──────────────────────────────
    def fetch_upcoming_digest(self, identity: str, hours: int = 72, max_results: int = 10) -> CalendarDigest:
        """Upcoming events visible in the linked calendar. Observe-tier."""
        entry = self._vault.require_entry(self.service, identity)
        time_min = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        listing = self.authenticated_request(
            identity,
            lambda at: self.transport.list_upcoming(at, max_results=max_results, time_min=time_min),
        )
        raw_items = listing.get("items", [])
        truncated = listing.get("nextPageToken") is not None
        events: list[CalendarEvent] = []
        for raw in raw_items:
            events.append(
                CalendarEvent(
                    event_id=raw.get("id", ""),
                    title=raw.get("summary") or "(untitled event)",
                    start_at=_parse_start(raw),
                    attendees=_attendee_names(raw),
                    urgency=_urgency(raw),
                    summary=_summary(raw),
                    location=raw.get("location"),
                    details=raw.get("description"),
                )
            )
        return CalendarDigest(
            account=self._identity_from_tokens(entry),
            window_hours=hours,
            total=len(events),
            truncated=truncated,
            events=events,
        )


def _parse_start(raw: dict) -> datetime:
    iso = raw.get("start", {}).get("dateTime") or raw.get("start", {}).get("date")
    if not iso:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)