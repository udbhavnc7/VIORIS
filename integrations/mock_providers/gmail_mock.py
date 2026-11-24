"""
Scripted mock Gmail transport for tests (Phase 6, Prompt 6.1).

Implements the GmailTransport protocol with scripted messages and injectable
failures, so connector tests never touch the network. Records every call and
the OAuth scope the connector requested, so tests can assert the connector
stayed inside its declared read-only scope.
"""

from __future__ import annotations

import copy

from integrations.base import ConnectorError, ExpiredSessionError, RateLimitError


class MockGmailTransport:
    """A Gmail-shaped provider with canned inbox + failure injection."""

    FAIL_RATE_LIMIT = "rate_limit"
    FAIL_EXPIRED = "expired"

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.name = "gmail"
        #: canned messages keyed by message id (already Gmail-shaped metadata).
        #: Deep-copied so a test that stars/labels a message cannot mutate the
        #: module-level defaults and leak into later tests.
        source = messages if messages is not None else _DEFAULT_MESSAGES
        self.messages = {m["id"]: copy.deepcopy(m) for m in source}
        self.calls: list[str] = []
        self.revoked: bool = False
        #: write-path records, so tests can prove nothing fired on a refusal
        self.modified: list[dict] = []
        self.sent: list[dict] = []
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

    # ── write endpoints ──────────────────────────────────────────────────────
    def modify_message(
        self,
        access_token: str,
        message_id: str,
        *,
        add_labels: tuple[str, ...] = (),
        remove_labels: tuple[str, ...] = (),
    ) -> dict:
        self.calls.append(f"modify_message:{message_id}")
        self._maybe_fail()
        try:
            message = self.messages[message_id]
        except KeyError as exc:
            raise ConnectorError(f"no message {message_id}") from exc
        labels = set(message.get("labelIds", ["INBOX", "UNREAD"]))
        labels.update(add_labels or ())
        labels.difference_update(remove_labels or ())
        message["labelIds"] = sorted(labels)
        self.modified.append(
            {
                "message_id": message_id,
                "add_labels": list(add_labels or ()),
                "remove_labels": list(remove_labels or ()),
                "label_ids": sorted(labels),
            }
        )
        return {"id": message_id, "labelIds": sorted(labels)}

    def send_message(
        self,
        access_token: str,
        *,
        raw: str,
        thread_id: str | None = None,
    ) -> dict:
        self.calls.append("send_message")
        self._maybe_fail()
        message_id = f"sent_{len(self.sent) + 1}"
        record = {
            "message_id": message_id,
            "thread_id": thread_id or "t0",
            "raw": raw,
        }
        self.sent.append(record)
        return {"id": message_id, "threadId": record["thread_id"], "labelIds": ["SENT"]}

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
        "labelIds": ["INBOX", "UNREAD"],
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
        "labelIds": ["INBOX", "UNREAD"],
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
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Recruiter <recruiter@example.com>"},
                {"name": "Subject", "value": "Interview scheduling"},
            ]
        },
        "snippet": "Can you let me know which day works for you?",
    },
]