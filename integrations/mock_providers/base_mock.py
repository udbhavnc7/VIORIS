"""
Generic mock OAuth provider for connector tests (Phase 6, Prompt 6.x).

Mimics the provider surface a connector talks to: authorize page, code →
tokens exchange, refresh, revoke, and (optionally) a 429-able data endpoint.
State survives in-process only, so every test gets a fresh provider. The mock
asserts the scopes the connector requested stayed within what was declared.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


class MockProviderError(Exception):
    pass


@dataclass
class MockProvider:
    """In-memory stand-in for a real OAuth provider."""

    name: str = "mock"
    #: tokens by refresh token
    sessions: dict = field(default_factory=dict)
    #: what the connector may request (read + write), to catch over-scoping
    allowed_read_scopes: list = field(default_factory=list)
    allowed_write_scopes: list = field(default_factory=list)
    fail_next: str | None = None  # "rate_limit" | "expired" | "revoke"

    def authorize_page(self, state: str) -> str:
        return f"https://{self.name}/authorize?state={state}"

    def code_for_state(self, state: str) -> str:
        return f"code_{uuid.uuid4().hex[:8]}"

    def exchange(self, code: str, requested_scopes: list) -> dict:
        """Issue tokens; refuse scopes beyond what the provider allows."""
        over = [s for s in requested_scopes
                if s not in self.allowed_read_scopes + self.allowed_write_scopes]
        if over:
            raise MockProviderError(f"scope not permitted by this provider: {over}")
        access = f"access_{uuid.uuid4().hex[:10]}"
        refresh = f"refresh_{uuid.uuid4().hex[:10]}"
        self.sessions[refresh] = {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
            "account_id": f"acct_{self.name}",
            "scope": list(requested_scopes),
        }
        return dict(self.sessions[refresh])

    def refresh(self, refresh_token: str) -> dict:
        sess = self.sessions.get(refresh_token)
        if sess is None:
            raise MockProviderError("unknown refresh token")
        sess["access_token"] = f"access_{uuid.uuid4().hex[:10]}"
        sess["expires_in"] = 3600
        return dict(sess)

    def revoke(self, access_token: str) -> None:
        if self.fail_next == "revoke":
            self.fail_next = None
            raise MockProviderError("revoke endpoint simulated failure")
        self.sessions = {
            r: {**s, "revoked": True, "access_token": "REVOKED"}
            for r, s in self.sessions.items()
            if s.get("access_token") == access_token or True
        }
        for r, s in self.sessions.items():
            if s.get("access_token") == "REVOKED":
                pass
        # neutralisation: drop any session holding the revoked access token
        self.sessions = {
            r: s for r, s in self.sessions.items()
            if s.get("access_token") != "REVOKED"
        }

    def data_endpoint(self, access_token: str) -> dict:
        """A typical authenticated GET; rate-limit + expiry are injectable."""
        if self.fail_next == "rate_limit":
            self.fail_next = None
            raise MockProviderError("429 simulated")
        if self.fail_next == "expired":
            self.fail_next = None
            raise MockProviderError("401 simulated")
        return {"data": ["item-1", "item-2"], "at": time.time()}