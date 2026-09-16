"""
Scripted mock Drive transport for tests (Phase 6).

Implements the DriveTransport protocol with canned recent files and injectable
failures, so connector tests never touch the network. Records the scope
requested so tests can assert read-only metadata discipline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from integrations.base import ConnectorError, ExpiredSessionError, RateLimitError


class MockDriveTransport:
    """A Google-Drive-shaped provider with canned files + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self, files: list[dict] | None = None) -> None:
        self.name = "cloud_files"
        self.files = files or _DEFAULT_FILES
        self.calls: list[str] = []
        self.revoked: bool = False
        self._auth_scope: str = ""
        self.fail_next: str | None = None
        self._sessions: dict[str, dict] = {}

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str:
        self._auth_scope = scope
        return f"https://drive.mock/authorize?state={state}&scope={scope}"

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

    def list_recent(self, access_token: str, *, max_results: int = 20, query: str = "") -> dict:
        self.calls.append(f"list_recent:{access_token}")
        self._maybe_fail()
        items = self.files[:max_results]
        return {
            "files": items,
            "nextPageToken": "p1" if len(self.files) > max_results else None,
        }

    def _maybe_fail(self) -> None:
        if self.fail_next == self.FAIL_RATE_LIMIT:
            self.fail_next = None
            raise RateLimitError("mock 429")
        if self.fail_next == self.FAIL_EXPIRED:
            self.fail_next = None
            raise ExpiredSessionError("mock 401 access token invalid")


now = datetime.now(UTC)
_DEFAULT_FILES = [
    {
        "id": "f1",
        "name": "Q3 Budget.xlsx",
        "mimeType": "application/vnd.ms-excel",
        "owners": [{"displayName": "mock-user@gmail.com"}],
        "modifiedTime": (now - timedelta(hours=2)).isoformat(),
    },
    {
        "id": "f2",
        "name": "Research Notes",
        "mimeType": "application/vnd.google-apps.folder",
        "owners": [{"displayName": "mock-user@gmail.com"}],
        "modifiedTime": (now - timedelta(hours=5)).isoformat(),
    },
    {
        "id": "f3",
        "name": "Launch Deck.pdf",
        "mimeType": "application/pdf",
        "owners": [{"displayName": "Colleague", "emailAddress": "colleague@example.com"}],
        "modifiedTime": (now - timedelta(hours=30)).isoformat(),
    },
]