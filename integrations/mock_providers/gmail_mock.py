"""
Scripted mock Gmail transport for tests (Phase 6, Prompt 6.1).

Implements the GmailTransport protocol with scripted messages and injectable
failures, so connector tests never touch the network. Records every call and
the OAuth scope the connector requested, so tests can assert the connector
stayed inside its declared read-only scope.
"""

from __future__ import annotations

from integrations.base import ConnectorError, ExpiredSessionError, RateLimitError


class MockGmailTransport:
    """A Gmail-shaped provider with canned inbox + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.name = "gmail"
        #: canned messages keyed by message id (already Gmail-shaped metadata)
        self.messages = {m["id"]: m for m in (messages or _DEFAULT_MESSAGES)}
        self.calls: list[str] = []
        self.revoked: bool = False
        self.require_scope: str | None = None  # set by authorize_uri; must be respected
        self.fail_next: str | None = None
        self._auth_scope: str = ""
        # OAuth state maintained in-memory (test-only)
        self._sessions: dict[str, dict] = {}

    # ── token plumbing (mock) ───────────────────────────────────────────────
    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str:
        self._auth_state = state
        self._auth_scope = scope
        return f"https://gmail.mock/authorize?state={state}&scope={scope}"

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        # remember which scope was asked for; tests assert it is read-only
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

    # ── Gmail data endpoints ─────────────────────────────────────────────────
    def list_unread(self, access_token: str, *, max_results: int = 20, query: str = "") -> dict:
        self.calls.append(f"list_unread:{access_token}")
        self._maybe_fail()
        ids = list(self.messages)
        ids = ids[:max_results]
        return {
            "messages": [{"id": mid, "threadId": f"t{i}"} for i, mid in enumerate(ids)],
            "resultSizeEstimate": len(self.messages),
            "nextPageToken": "p1" if len(self.messages) > max_results else None,
        }

    def get_metadata(self, access_token: str, message_id: str) -> dict:
        self.calls.append(f"get_metadata:{message_id}")
        self._maybe_fail()
        try:
            return self.messages[message_id]
        except KeyError as exc:
            raise ConnectorError(f"no message {message_id}") from exc

    # ── failure injection ────────────────────────────────────────────────────
    def _maybe_fail(self) -> None:
        if self.fail_next == self.FAIL_RATE_LIMIT:
            self.fail_next = None
            raise RateLimitError("mock 429")
        if self.fail_next == self.FAIL_EXPIRED:
            self.fail_next = None
            raise ExpiredSessionError("mock 401 access token invalid")


_DEFAULT_MESSAGES = [
    {
        "id": "m1",
        "payload": {
            "headers": [
                {"name": "From", "value": "Boss <boss@example.com>"},
                {"name": "Subject", "value": "URGENT: action required on the launch"},
            ]
        },
        "snippet": "Please confirm by tomorrow or we miss the deadline",
    },
    {
        "id": "m2",
        "payload": {
            "headers": [
                {"name": "From", "value": "Newsletter <news@example.com>"},
                {"name": "Subject", "value": "Weekly digest"},
            ]
        },
        "snippet": "Here are this week's stories.",
    },
    {
        "id": "m3",
        "payload": {
            "headers": [
                {"name": "From", "value": "Recruiter <recruiter@example.com>"},
                {"name": "Subject", "value": "Interview scheduling"},
            ]
        },
        "snippet": "Can you let me know which day works for you?",
    },
]