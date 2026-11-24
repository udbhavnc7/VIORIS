"""
Gmail write connector — reactions and replies, kept separate from the read path.

The read-only `GmailConnector` never gains a write scope. This connector declares
its own write scopes (`gmail.modify` + `gmail.send`) and exposes exactly two
tools, both Execute-tier and approval-gated:

  - `gmail.react`      star / label / mark-read ONE message (never a reply)
  - `gmail.send_reply` send an approved reply in an existing thread

Why `gmail.react` is Execute rather than Prepare: Gmail has no native reaction,
so "react" maps to an externally visible mailbox change (star, label, mark
read). This repo's tier table defines Prepare as "nothing external happens", so
the mutation is Execute with its own diff card. The roadmap suggested Prepare as
a possibility; this implementation chooses the stricter, documented tier.

No shortcut exists: the connector calls `require_approval_gate("execute", …)`
and the API layer wires an `approval_verifier` to the task-runner ledger. The
transport is injected, so tests never touch the network.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    ExpiredSessionError,
    OAuthScopes,
    PermissionNotApprovedError,
)

from .connector import SCOPE_READONLY

#: Minimum-viable write scopes, declared separately from the read scope.
SCOPE_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"

#: Friendly explanation surfaced in the connected-accounts panel.
PERMISSION_EXPLANATION = (
    "React to one email (star, label, or mark read) and send replies you "
    "approve. Vioris cannot delete, forward, or send anything without an "
    "approved action."
)

#: Gmail has no native thumbs-up. These are the deliberate, reversible mappings.
#: Each entry is (labels to add, labels to remove).
_REACTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "acknowledge": (("STARRED",), ("UNREAD",)),  # star + mark read
    "star": (("STARRED",), ()),
    "unstar": ((), ("STARRED",)),
    "mark_read": ((), ("UNREAD",)),
    "mark_unread": (("UNREAD",), ()),
}
_REACTION_LABEL = "label"  # explicit label id, supplied by the caller

#: Header injection guard: CR/LF in a header value is never legitimate here.
_HEADER_BREAK = re.compile(r"[\r\n]")


class GmailWriteTransport(Protocol):
    """The write surface the connector needs. Tests inject a scripted mock."""

    def modify_message(
        self,
        access_token: str,
        message_id: str,
        *,
        add_labels: tuple[str, ...] = (),
        remove_labels: tuple[str, ...] = (),
    ) -> dict: ...

    def send_message(
        self,
        access_token: str,
        *,
        raw: str,
        thread_id: str | None = None,
    ) -> dict: ...


@dataclass
class ReactResult:
    message_id: str
    reaction: str
    added_labels: list[str]
    removed_labels: list[str]
    idempotency_key: str
    replay: bool = False


@dataclass
class ReplyResult:
    message_id: str
    thread_id: str
    to: str
    subject: str
    idempotency_key: str
    replay: bool = False


def _assert_single_line(field: str, value: str) -> None:
    if _HEADER_BREAK.search(value):
        raise ConnectorError(f"{field} must not contain line breaks")


def _require_write_scope(entry: dict, scope: str, action: str) -> None:
    """Fail before the provider call if the user only granted read scope.

    This is a clarity guard, not the security boundary: Gmail itself would also
    reject the call. It keeps a read-only connection from producing a confusing
    provider error when the fix is simply to reconnect with write scope.
    """
    if scope not in (entry.get("scopes") or []):
        raise ConnectorError(
            f"{action} requires the Gmail write scope {scope}; "
            f"reconnect with write scope enabled"
        )


def build_raw_message(*, to: str, subject: str, body: str) -> str:
    """Build the base64url RFC822 payload Gmail's send endpoint expects.

    Header values are validated so a caller cannot inject extra headers through
    a newline in the recipient or subject.
    """
    _assert_single_line("to", to)
    _assert_single_line("subject", subject)
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


class GmailWriteConnector(AbstractConnector):
    """Write-only companion to the read-only Gmail connector."""

    service = "gmail"
    auth_url = ""
    token_url = ""
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["gmail.react", "gmail.send_reply"]

    def __init__(self, vault, transport: GmailWriteTransport | None = None, **_ignored) -> None:
        super().__init__(vault, "")
        self.transport = transport

    def declared_scopes(self) -> OAuthScopes:
        # Write scopes are declared here and never merged into the read
        # connector, whose tests assert an empty write list.
        return OAuthScopes(
            read=[SCOPE_READONLY],
            write=[SCOPE_MODIFY, SCOPE_SEND],
        )

    # ── OAuth lifecycle hooks (same provider, different scope request) ─────
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        if self.transport is None:
            raise ConnectorError("no Gmail write transport configured")
        return self.transport.authorize_uri(state, redirect_uri, " ".join(self._scopes.all()))

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        if self.transport is None:
            raise ConnectorError("no Gmail write transport configured")
        return self.transport.exchange_code(code, redirect_uri)

    def refresh_access_token(self, refresh_token: str) -> dict:
        if self.transport is None:
            raise ConnectorError("no Gmail write transport configured")
        return self.transport.refresh_access_token(refresh_token)

    def revoke(self, access_token: str) -> None:
        if self.transport is not None:
            self.transport.revoke(access_token)

    # ── reactions (Execute-tier) ────────────────────────────────────────────
    def react(
        self,
        identity: str,
        *,
        message_id: str,
        reaction: str = "acknowledge",
        label: str | None = None,
        idempotency_key: str,
        approval_verifier=None,
    ) -> ReactResult:
        """Apply one reversible reaction to one message.

        Refuses unless the registry says EXECUTE and the ledger verifier confirms
        an approval for this step+key. Replaying the same key returns the prior
        result instead of touching the mailbox twice.
        """
        if not message_id or not message_id.strip():
            raise ConnectorError("message_id is required")
        reaction = (reaction or "acknowledge").strip().lower()

        if reaction == _REACTION_LABEL:
            if not label or not label.strip():
                raise ConnectorError("a label id is required when reaction is 'label'")
            add_labels: tuple[str, ...] = (label.strip(),)
            remove_labels: tuple[str, ...] = ()
        else:
            try:
                add_labels, remove_labels = _REACTIONS[reaction]
            except KeyError as exc:
                allowed = ", ".join(sorted([*_REACTIONS, _REACTION_LABEL]))
                raise ConnectorError(
                    f"unknown reaction {reaction!r}; choose one of: {allowed}"
                ) from exc

        if self.transport is None:
            raise ConnectorError("no Gmail write transport configured")
        entry = self._vault.require_entry(self.service, identity)
        if entry.get("expired"):
            raise ExpiredSessionError(f"{identity} session expired — reconnect required")
        _require_write_scope(entry, SCOPE_MODIFY, "reacting to an email")

        prior = self._vault.load("gmail_reactions", idempotency_key)
        if prior and prior.get("status") == "applied":
            return ReactResult(
                message_id=prior["message_id"],
                reaction=prior["reaction"],
                added_labels=list(prior["added_labels"]),
                removed_labels=list(prior["removed_labels"]),
                idempotency_key=idempotency_key,
                replay=True,
            )

        self.require_approval_gate(
            "execute",
            "gmail.react",
            {
                "message_id": message_id,
                "reaction": reaction,
                "label": label,
            },
        )
        if approval_verifier is not None and not approval_verifier():
            raise PermissionNotApprovedError(
                "no approved approval exists for this reaction (task-runner ledger)"
            )

        body = self.authenticated_request(
            identity,
            lambda at: self.transport.modify_message(
                at,
                message_id,
                add_labels=add_labels,
                remove_labels=remove_labels,
            ),
        )
        applied = sorted(set(body.get("labelIds") or []))
        self._vault.save(
            "gmail_reactions",
            idempotency_key,
            {
                "message_id": message_id,
                "reaction": reaction,
                "added_labels": list(add_labels),
                "removed_labels": list(remove_labels),
                "label_ids": applied,
                "status": "applied",
            },
        )
        self._audit(
            identity,
            "reacted_to_email",
            {
                "message_id": message_id,
                "reaction": reaction,
                "added_labels": list(add_labels),
                "removed_labels": list(remove_labels),
                "idempotency_key": idempotency_key,
            },
        )
        return ReactResult(
            message_id=message_id,
            reaction=reaction,
            added_labels=list(add_labels),
            removed_labels=list(remove_labels),
            idempotency_key=idempotency_key,
        )

    # ── replies (Execute-tier) ──────────────────────────────────────────────
    def send_reply(
        self,
        identity: str,
        *,
        thread_id: str,
        to: str,
        subject: str,
        body: str,
        idempotency_key: str,
        approval_verifier=None,
    ) -> ReplyResult:
        """Send one approved reply in an existing thread.

        The body and recipient are validated before the gate so an empty or
        malformed reply fails locally; a replayed key returns the prior result
        rather than sending twice.
        """
        if not thread_id or not thread_id.strip():
            raise ConnectorError("thread_id is required to reply in-thread")
        if not to or not to.strip():
            raise ConnectorError("refusing to send a reply with no recipient")
        if not subject or not subject.strip():
            raise ConnectorError("refusing to send a reply with no subject")
        if not body or not body.strip():
            raise ConnectorError("refusing to send an empty reply")
        _assert_single_line("to", to)
        _assert_single_line("subject", subject)

        if self.transport is None:
            raise ConnectorError("no Gmail write transport configured")
        entry = self._vault.require_entry(self.service, identity)
        if entry.get("expired"):
            raise ExpiredSessionError(f"{identity} session expired — reconnect required")
        _require_write_scope(entry, SCOPE_SEND, "sending a reply")

        prior = self._vault.load("gmail_replies", idempotency_key)
        if prior and prior.get("status") == "sent":
            return ReplyResult(
                message_id=prior["message_id"],
                thread_id=prior["thread_id"],
                to=prior["to"],
                subject=prior["subject"],
                idempotency_key=idempotency_key,
                replay=True,
            )

        self.require_approval_gate(
            "execute",
            "gmail.send_reply",
            {
                "thread_id": thread_id,
                "to": to,
                "subject": subject,
                "body": body,
            },
        )
        if approval_verifier is not None and not approval_verifier():
            raise PermissionNotApprovedError(
                "no approved approval exists for this reply (task-runner ledger)"
            )

        raw = build_raw_message(to=to, subject=subject, body=body)
        sent = self.authenticated_request(
            identity,
            lambda at: self.transport.send_message(at, raw=raw, thread_id=thread_id),
        )
        message_id = str(sent.get("id") or "")
        if not message_id:
            raise ConnectorError("Gmail did not return a message id — treat as unverified")
        actual_thread = str(sent.get("threadId") or thread_id)

        self._vault.save(
            "gmail_replies",
            idempotency_key,
            {
                "message_id": message_id,
                "thread_id": actual_thread,
                "to": to,
                "subject": subject,
                "status": "sent",
            },
        )
        self._audit(
            identity,
            "sent_email_reply",
            {
                "message_id": message_id,
                "thread_id": actual_thread,
                "to": to,
                "subject": subject,
                "idempotency_key": idempotency_key,
            },
        )
        return ReplyResult(
            message_id=message_id,
            thread_id=actual_thread,
            to=to,
            subject=subject,
            idempotency_key=idempotency_key,
        )
