"""
Cloud files connector — READ-ONLY (Phase 6, order: … → Cloud files → …).

Minimum-viable scope: `drive.metadata.readonly` (names, owners, times —
never file content by default). It lists/search recent files and produces a
digest. There are NO write scopes; uploading, editing, or deleting files is
intentionally unregistered, so any such path is blocked by the permission
engine.

OAuth + token handling mirror the Gmail connector exactly: encrypted vault,
transparent single refresh, loud rate-limit / expired-session failures, and an
injected transport so tests use a scripted mock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    OAuthScopes,
    RateLimitError,
)

_DEFAULT_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
_DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE_API = "https://www.googleapis.com/drive/v3"

# Minimum-viable read scope: file METADATA only (names, owners, times).
SCOPE_READONLY = "https://www.googleapis.com/auth/drive.metadata.readonly"

PERMISSION_EXPLANATION = (
    "Read only your cloud file listing: names, owners, and last-modified "
    "times. It cannot read file contents, upload, edit, or delete anything."
)


class DriveTransport(Protocol):
    """The small HTTP surface a cloud-files connector needs (mockable)."""

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> dict: ...

    def refresh_access_token(self, refresh_token: str) -> dict: ...

    def revoke(self, access_token: str) -> None: ...

    def list_recent(
        self, access_token: str, *, max_results: int = 20, query: str = ""
    ) -> dict:
        """GET files.list → {'files': [...], 'nextPageToken': ...}."""


@dataclass
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    owner: str | None
    modified_at: datetime
    is_folder: bool
    summary: str


@dataclass
class FileDigest:
    account: str
    window_hours: int
    total: int
    truncated: bool
    files: list[DriveFile]


def _owner(entry: dict) -> str | None:
    owners = entry.get("owners") or []
    if owners:
        return owners[0].get("displayName") or owners[0].get("emailAddress")
    return None


def _modified_at(entry: dict) -> datetime:
    try:
        return datetime.fromisoformat((entry.get("modifiedTime") or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


class HttpxDriveTransport:
    """Production transport against Google's OAuth + Drive REST APIs."""

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
            "fields": "files(id,name,mimeType,owners,modifiedTime),nextPageToken",
        }
        if query:
            params["q"] = query
        resp = self._httpx.get(
            f"{_DRIVE_API}/files",
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


class DriveConnector(AbstractConnector):
    """Read-only cloud files integration. Tools: cloud_files.list_recent."""

    service = "cloud_files"
    auth_url = _DEFAULT_AUTH_URL
    token_url = _DEFAULT_TOKEN_URL
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["cloud_files.list_recent"]

    def __init__(self, vault, transport: DriveTransport | None = None,
                 token_endpoint: str | None = None, **_ignored) -> None:
        super().__init__(vault, token_endpoint or self.token_url)
        self.transport = transport or HttpxDriveTransport("", "")

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

    def fetch_recent_digest(self, identity: str, hours: int = 72, max_results: int = 20) -> FileDigest:
        """Recently modified files (metadata only). Observe-tier."""
        entry = self._vault.require_entry(self.service, identity)
        since = (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        query = f"modifiedTime >= '{since}' and trashed = false"
        listing = self.authenticated_request(
            identity,
            lambda at: self.transport.list_recent(at, max_results=max_results, query=query),
        )
        raw = listing.get("files", [])
        truncated = listing.get("nextPageToken") is not None
        files = [
            DriveFile(
                file_id=f.get("id", ""),
                name=f.get("name", "(untitled)"),
                mime_type=f.get("mimeType", "application/octet-stream"),
                owner=_owner(f),
                modified_at=_modified_at(f),
                is_folder=f.get("mimeType") == "application/vnd.google-apps.folder",
                summary=f"{f.get('name', '(untitled)')} — modified {_modified_at(f).strftime('%b %d, %H:%M')}",
            )
            for f in raw
        ]
        return FileDigest(
            account=self._identity_from_tokens(entry),
            window_hours=hours,
            total=len(files),
            truncated=truncated,
            files=files,
        )