"""
Notes/documents connector — READ-ONLY (Phase 6, order: … → Notes/documents → …).

Minimum-viable scope: `docs.readonly` (view notes, titles, last-edited times,
and note bodies). It lists recent notes and produces a digest. There are NO
write scopes; creating, editing, or deleting notes is intentionally
unregistered, so any such path is blocked by the permission engine.

OAuth + token handling mirror the Gmail connector exactly: encrypted vault,
transparent single refresh, loud rate-limit / expired-session failures, and an
injected transport so tests use a scripted mock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    OAuthScopes,
    RateLimitError,
)

_DEFAULT_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
_DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DOCS_API = "https://docs.googleapis.com/v1"

# Minimum-viable read scope.
SCOPE_READONLY = "https://www.googleapis.com/auth/documents.readonly"

PERMISSION_EXPLANATION = (
    "Read only your notes and documents: titles, last-edited times, and note "
    "bodies. It cannot create, edit, or delete anything in your documents."
)


class NotesTransport(Protocol):
    """The small HTTP surface a notes connector needs (mockable)."""

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> dict: ...

    def refresh_access_token(self, refresh_token: str) -> dict: ...

    def revoke(self, access_token: str) -> None: ...

    def list_recent(
        self, access_token: str, *, max_results: int = 20, query: str = ""
    ) -> dict:
        """GET files.list → {'files': [...], 'nextPageToken': ...}."""

    def fetch_note(self, access_token: str, note_id: str) -> dict:
        """GET a single note body."""


@dataclass
class Note:
    note_id: str
    title: str
    updated_at: datetime
    summary: str
    body: str = ""


@dataclass
class NotesDigest:
    account: str
    window_hours: int
    total: int
    truncated: bool
    notes: list[Note]


def _updated_at(entry: dict) -> datetime:
    try:
        return datetime.fromisoformat((entry.get("modifiedTime") or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _body_text(doc: dict) -> str:
    content = doc.get("body", {}).get("content", [])
    parts = []
    for el in content:
        for run in el.get("paragraph", {}).get("elements", []) or []:
            text = run.get("textRun", {}).get("content") or ""
            if text.strip():
                parts.append(text)
    return "".join(parts).strip()


def _summary(title: str, body: str) -> str:
    if not body:
        return title
    flat = body.replace("\n", " ")
    if len(flat) <= 120:
        return flat
    return flat[:117].rstrip() + "…"


class HttpxNotesTransport:
    """Production transport against Google's OAuth + Docs REST APIs."""

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

    def list_recent(self, access_token: str, *, max_results: int = 20, query: str = "") -> dict:
        params = {
            "maxResults": str(max_results),
            "orderBy": "modifiedTime desc",
            "fields": "files(id,name,mimeType,modifiedTime),nextPageToken",
        }
        if query:
            params["q"] = query
        resp = self._httpx.get(
            "https://www.googleapis.com/drive/v3/files",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("list_recent: 429")
        if resp.status_code in (401, 403):
            raise ConnectorError("expired: access token rejected")
        resp.raise_for_status()
        return resp.json()

    def fetch_note(self, access_token: str, note_id: str) -> dict:
        resp = self._httpx.get(
            f"{_DOCS_API}/documents/{note_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if resp.status_code == 429:
            raise RateLimitError("fetch_note: 429")
        if resp.status_code in (401, 403):
            raise ConnectorError("expired: access token rejected")
        resp.raise_for_status()
        return resp.json()


class NotesConnector(AbstractConnector):
    """Read-only notes/documents integration. Tools: notes.list_recent."""

    service = "notes"
    auth_url = _DEFAULT_AUTH_URL
    token_url = _DEFAULT_TOKEN_URL
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["notes.list_recent"]

    def __init__(self, vault, transport: NotesTransport | None = None,
                 token_endpoint: str | None = None, **_ignored) -> None:
        super().__init__(vault, token_endpoint or self.token_url)
        self.transport = transport or HttpxNotesTransport("", "")

    def declared_scopes(self) -> OAuthScopes:
        # READ-ONLY: write scopes intentionally empty. Never merge.
        return OAuthScopes(read=[SCOPE_READONLY], write=[])

    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        return self.transport.authorize_uri(state, redirect_uri, " ".join(self._scopes.read))

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        return self.transport.exchange_code(code, redirect_uri)

    def refresh_access_token(self, refresh_token: str) -> dict:
        return self.transport.refresh_access_token(refresh_token)

    def revoke(self, access_token: str) -> None:
        self.transport.revoke(access_token)

    def fetch_recent_digest(self, identity: str, hours: int = 168, max_results: int = 20) -> NotesDigest:
        """Recently edited notes + bodies. Observe-tier."""
        entry = self._vault.require_entry(self.service, identity)
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        query = f"modifiedTime >= '{since}' and trashed = false"
        listing = self.authenticated_request(
            identity,
            lambda at: self.transport.list_recent(at, max_results=max_results, query=query),
        )
        raw = listing.get("files", [])
        truncated = listing.get("nextPageToken") is not None
        notes: list[Note] = []
        for f in raw:
            doc = self.authenticated_request(
                identity,
                lambda at, fid=f["id"]: self.transport.fetch_note(at, fid),
            )
            body = _body_text(doc)
            title = f.get("name") or "(untitled)"
            notes.append(
                Note(
                    note_id=f.get("id", ""),
                    title=title,
                    updated_at=_updated_at(f),
                    summary=_summary(title, body),
                    body=body[:500],
                )
            )
        return NotesDigest(
            account=self._identity_from_tokens(entry),
            window_hours=hours,
            total=len(notes),
            truncated=truncated,
            notes=notes,
        )