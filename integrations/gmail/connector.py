"""
Gmail connector — READ-ONLY FIRST (Phase 6, Prompt 6.1).

Minimium-viable scope: `gmail.readonly`. It can fetch/digest unread mail and
nothing else. There are NO write scopes declared on this connector, so there is
nothing to accidentally send; any future send/reply capability must (a) add a
separate write scope, (b) go through the permission engine as Execute, and
(c) never reuse this READ path.

The connector talks to Google only through an injected `transport`, so tests
feed it a scripted mock provider (integrations/mock_providers/). Nothing here
ever returns or logs a token — the vault owns that material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    OAuthScopes,
    RateLimitError,
)

# Google OAuth + Gmail endpoints
_DEFAULT_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
_DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# Minimum-viable read scope. Read-only connector intentionally has NO write scope.
SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"

#: Friendly explanation surfaced in the connected-accounts panel.
PERMISSION_EXPLANATION = (
    "Read only your Gmail: shows subject lines, senders, and snippets of your "
    "unread mail. It cannot send, delete, or change anything in your account."
)


class GmailTransport(Protocol):
    """The small HTTP surface a Gmail connector needs. Tests implement this
    with a scripted mock; production uses HttpxGmailTransport."""

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> dict: ...

    def refresh_access_token(self, refresh_token: str) -> dict: ...

    def revoke(self, access_token: str) -> None: ...

    def list_unread(
        self, access_token: str, *, max_results: int = 20, query: str = ""
    ) -> dict:
        """GET gmail messages.list → {'messages': [{id, threadId}], 'resultSizeEstimate': n}."

        Raises RateLimitError on 429, ConnectorError containing 'expired' on a
        non-refreshable 401."""


# ─── digest shapes ────────────────────────────────────────────────────────────


@dataclass
class MailDigestItem:
    message_id: str
    sender: str
    subject: str
    urgency: str  # "low" | "medium" | "high"
    snippet: str
    asks: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    received_at: datetime | None = None


@dataclass
class MailDigest:
    account: str
    window_hours: int
    total_unread: int
    truncated: bool
    items: list[MailDigestItem]


# Local, deterministic heuristics — no external model, no per-request API cost.


def _urgency(subject: str, snippet: str) -> str:
    text = f"{subject} {snippet}".lower()
    high = ("urgent", "asap", "as soon as possible", "immediately", "deadline today",
            "important", "action required", "expiring tonight", "critical")
    medium = ("due", "please review", "by tomorrow", "reminder", "follow up", "eod")
    if any(h in text for h in high):
        return "high"
    if any(m in text for m in medium):
        return "medium"
    return "low"


_ASK_PATTERNS = [
    r"please (confirm|reply|let me know|review|take a look)",
    r"(can|could) you (confirm|send|review|check|advise|let me know)",
    r"(deadline|due) (is |by )?([a-z]+ ?\d{1,2}(st|nd|rd|th)?)?",
    r"i need (this|it) (by|before)",
]


def _extract_asks(subject: str, snippet: str) -> list[str]:
    text = f"{subject} {snippet}".lower()
    asks: list[str] = []
    for pat in _ASK_PATTERNS:
        for m in re.finditer(pat, text):
            asks.append(m.group(0).strip())
    return asks[:3]


_DATES_PATTERN = r"(\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*|\b\d{1,2}/\d{1,2}\b|\btomorrow\b|\btoday\b|\bby [a-z]+ \d{1,2}(st|nd|rd|th)?\b)"

def _extract_dates(subject: str, snippet: str) -> list[str]:
    found = re.findall(_DATES_PATTERN, f"{subject} {snippet}".lower())
    return list(dict.fromkeys(f[0] for f in found))[:3]


# ─── real transport (httpx) ───────────────────────────────────────────────────


class HttpxGmailTransport:
    """Production transport against Google's OAuth + Gmail REST APIs."""

    def __init__(self, client_id: str, client_secret: str,
                 auth_url: str = _DEFAULT_AUTH_URL,
                 token_url: str = _DEFAULT_TOKEN_URL) -> None:
        import httpx  # imported lazily so the connector imports without deps
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

    def list_unread(self, access_token: str, *, max_results: int = 20, query: str = "") -> dict:
        params = {
            "maxResults": str(max_results),
            "q": query or "is:unread",
            "labelIds": "INBOX",
        }
        resp = self._httpx.get(
            f"{_GMAIL_API}/messages",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("list_unread: 429")
        if resp.status_code in (401, 403):
            raise ConnectorError("expired: access token rejected")
        resp.raise_for_status()
        return resp.json()

    def get_metadata(self, access_token: str, message_id: str) -> dict:
        resp = self._httpx.get(
            f"{_GMAIL_API}/messages/{message_id}",
            params={"format": "metadata", "metadataHeaders": "From,Subject"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("get_metadata: 429")
        resp.raise_for_status()
        return resp.json()


# ─── connector ────────────────────────────────────────────────────────────────


class GmailConnector(AbstractConnector):
    """Read-only Gmail integration. Tools: gmail.read_unread."""

    service = "gmail"
    auth_url = _DEFAULT_AUTH_URL
    token_url = _DEFAULT_TOKEN_URL
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["gmail.read_unread"]

    def __init__(self, vault, transport: GmailTransport | None = None,
                 token_endpoint: str | None = None, **_ignored) -> None:
        super().__init__(vault, token_endpoint or self.token_url)
        self.transport = transport or HttpxGmailTransport("", "")

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
    def fetch_unread_digest(self, identity: str, hours: int = 24, max_results: int = 20) -> MailDigest:
        """Unread digest for the last `hours`. Observe-tier: no approval needed,
        changes nothing externally."""
        entry = self._vault.require_entry(self.service, identity)
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y/%m/%d")
        query = f"is:unread newer_than:{hours}h after:{since}"
        listing = self.authenticated_request(
            identity,
            lambda at: self.transport.list_unread(at, max_results=max_results, query=query),
        )
        messages = listing.get("messages", [])
        total = int(listing.get("resultSizeEstimate", len(messages)))
        truncated = listing.get("nextPageToken") is not None

        items: list[MailDigestItem] = []
        for msg in messages[:max_results]:
            detail = self.authenticated_request(
                identity,
                lambda at, m=msg["id"]: self.transport.get_metadata(at, m),
            )
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "(no subject)")
            sender = headers.get("From", "(unknown)")
            snippet = detail.get("snippet", "")
            items.append(
                MailDigestItem(
                    message_id=msg["id"],
                    sender=sender,
                    subject=subject,
                    urgency=_urgency(subject, snippet),
                    snippet=snippet[:180],
                    asks=_extract_asks(subject, snippet),
                    dates=_extract_dates(subject, snippet),
                    received_at=datetime.now(timezone.utc),
                )
            )
        return MailDigest(
            account=self._identity_from_tokens(entry),
            window_hours=hours,
            total_unread=total,
            truncated=truncated,
            items=items,
        )