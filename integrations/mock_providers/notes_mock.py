"""
Scripted mock Notes transport for tests (Phase 6).

Implements the NotesTransport protocol with canned documents and injectable
failures, so connector tests never touch the network. Records the scope
requested so tests can assert read-only discipline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from integrations.base import ConnectorError, ExpiredSessionError, RateLimitError


class MockNotesTransport:
    """A Google-Docs-shaped provider with canned notes + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self, notes: list[dict] | None = None) -> None:
        self.name = "notes"
        self.notes = notes or _DEFAULT_NOTES
        self.calls: list[str] = []
        self.revoked: bool = False
        self._auth_scope: str = ""
        self.fail_next: str | None = None
        self._sessions: dict[str, dict] = {}

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str:
        self._auth_scope = scope
        return f"https://docs.mock/authorize?state={state}&scope={scope}"

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
        items = self.notes[:max_results]
        return {
            "files": [{"id": n["id"], "name": n["title"], "modifiedTime": n["modifiedTime"]} for n in items],
            "nextPageToken": "p1" if len(self.notes) > max_results else None,
        }

    def fetch_note(self, access_token: str, note_id: str) -> dict:
        self.calls.append(f"fetch_note:{note_id}")
        self._maybe_fail()
        for n in self.notes:
            if n["id"] == note_id:
                return {
                    "documentId": note_id,
                    "title": n["title"],
                    "body": {"content": [
                        {"paragraph": {"elements": [
                            {"textRun": {"content": line}}
                        ]}}
                        for line in n["body_lines"]
                    ]},
                }
        raise ConnectorError(f"no note {note_id}")

    def _maybe_fail(self) -> None:
        if self.fail_next == self.FAIL_RATE_LIMIT:
            self.fail_next = None
            raise RateLimitError("mock 429")
        if self.fail_next == self.FAIL_EXPIRED:
            self.fail_next = None
            raise ExpiredSessionError("mock 401 access token invalid")


now = datetime.now(timezone.utc)
_DEFAULT_NOTES = [
    {
        "id": "n1",
        "title": "Grocery list",
        "modifiedTime": (now - timedelta(hours=3)).isoformat(),
        "body_lines": ["Milk", "Bread", "Eggs for the weekend"],
    },
    {
        "id": "n2",
        "title": "Project ideas",
        "modifiedTime": (now - timedelta(days=1)).isoformat(),
        "body_lines": ["Voice-first agent on the laptop", "Local model, $0 cost"],
    },
    {
        "id": "n3",
        "title": "Passwords (do not share)",
        "modifiedTime": (now - timedelta(days=3)).isoformat(),
        "body_lines": ["Rotate the drive token monthly"],
    },
]