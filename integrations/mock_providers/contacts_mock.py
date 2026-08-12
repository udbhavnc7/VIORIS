"""
Scripted mock Contacts transport for tests (Phase 6).

Implements the ContactsTransport protocol with canned contacts and injectable
failures, so connector tests never touch the network. Records the scope
requested so tests can assert read-only discipline.
"""

from __future__ import annotations

from integrations.base import ConnectorError, ExpiredSessionError, RateLimitError


class MockContactsTransport:
    """A Google-People-shaped provider with canned contacts + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self, contacts: list[dict] | None = None) -> None:
        self.name = "contacts"
        self.contacts = contacts or _DEFAULT_CONTACTS
        self.calls: list[str] = []
        self.revoked: bool = False
        self._auth_scope: str = ""
        self.fail_next: str | None = None
        self._sessions: dict[str, dict] = {}

    def authorize_uri(self, state: str, redirect_uri: str, scope: str) -> str:
        self._auth_scope = scope
        return f"https://contacts.mock/authorize?state={state}&scope={scope}"

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

    def list_contacts(self, access_token: str, *, max_results: int = 30, query: str = "") -> dict:
        self.calls.append(f"list_contacts:{access_token}")
        self._maybe_fail()
        items = self.contacts[:max_results]
        return {
            "connections": items,
            "nextPageToken": "p1" if len(self.contacts) > max_results else None,
        }

    def _maybe_fail(self) -> None:
        if self.fail_next == self.FAIL_RATE_LIMIT:
            self.fail_next = None
            raise RateLimitError("mock 429")
        if self.fail_next == self.FAIL_EXPIRED:
            self.fail_next = None
            raise ExpiredSessionError("mock 401 access token invalid")


_DEFAULT_CONTACTS = [
    {
        "resourceName": "people/c1",
        "names": [{"displayName": "Alice Chen"}],
        "emailAddresses": [{"value": "alice@example.com"}],
        "phoneNumbers": [{"value": "+1 555-0100"}],
    },
    {
        "resourceName": "people/c2",
        "names": [{"displayName": "Bob Miller"}],
        "emailAddresses": [{"value": "bob@example.com"}],
        "phoneNumbers": [],
    },
    {
        "resourceName": "people/c3",
        "names": [{"displayName": "Smile Clinic"}],
        "emailAddresses": [],
        "phoneNumbers": [{"value": "+1 555-0199"}],
    },
]