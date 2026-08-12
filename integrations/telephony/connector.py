"""
Telephony (calling) connector — Phase 6, order: … → Calling → …

V1 rule (CLAUDE.md / docs/08): NO server-initiated phone calls — Twilio-style
telephony costs money per minute. "Calling" in v1 means:
    prepare_call  -> contact lookup + talking-point brief + draft script (shown,
                     never dialed)
    start_call    -> hand off to the phone's NATIVE dialer (opens the dialer
                     with the number pre-filled; the user taps call). Execute
                     tier, approval-gated, idempotency-keyed. It never places
                     an autonomous call.

Both tools route through the permission engine: `telephony.prepare_call` is
registered statically at PREPARE (nothing external happens, the brief is shown)
and `telephony.start_call` at EXECUTE (externally visible handoff, diff card
must show the RESOLVED contact identity — never just the typed name). A call
never starts without an approved record in the task-runner ledger for that
step's idempotency_key, and re-playing a key returns the prior handoff instead
of opening the dialer twice.

The token vault stores the encrypted session reference; nothing sensitive is
exposed. Failures are explicit and loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    ExpiredSessionError,
    OAuthScopes,
    PermissionNotApprovedError,
    RateLimitError,
)

PERMISSION_EXPLANATION = (
    "Prepares a call: looks up the contact and drafts talking points and a "
    "script (shown to you, never dialed). Can also open your phone's native "
    "dialer pre-filled for the number, but only after you approve a diff card "
    "showing the resolved contact identity. It never places an autonomous call."
)

#: Browser-session-style capability surface (no OAuth).
_READ_SCOPE = "telephony:read-contacts"
_WRITE_SCOPE = "telephony:open-dialer"


@dataclass
class ContactInfo:
    name: str
    phone: str


@dataclass
class CallBrief:
    recipient: str
    recipient_identity: str  # resolved canonical identity (shown, never dialed)
    phone: str
    purpose: str
    talking_points: list[str] = field(default_factory=list)
    script: str = ""
    drafted: bool = True  # Prepare tier: shown, not sent


@dataclass
class CallHandoff:
    handoff_id: str
    recipient_identity: str
    phone: str
    channel: str  # always "native-dialer" in v1
    idempotency_key: str
    replay: bool = False


class TelephonyTransport(Protocol):
    """The dialer/contact surface a calling connector needs (mockable)."""

    def resolve_contact(self, recipient: str) -> ContactInfo:
        """Resolve the typed recipient to a canonical contact. Raises
        ConnectorError on ambiguous matches (the diff card must show the
        resolved identity; ambiguity forces a clarifying question)."""

    def open_dialer(self, phone: str, script: str) -> str:
        """Open the native dialer pre-filled for `phone` (v1 handoff, NOT an
        autonomous call). Returns a handoff id."""


class TelephonyConnector(AbstractConnector):
    """Calling connector. Tools: telephony.prepare_call (Prepare),
    telephony.start_call (Execute, approval-gated)."""

    service = "telephony"
    auth_url = ""  # no OAuth: native dialer + local contact lookup
    token_url = ""
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["telephony.prepare_call", "telephony.start_call"]

    def __init__(self, vault, transport: TelephonyTransport | None = None, **_ignored) -> None:
        super().__init__(vault, "")
        self.transport = transport

    def declared_scopes(self) -> OAuthScopes:
        return OAuthScopes(read=[_READ_SCOPE], write=[_WRITE_SCOPE])

    # OAuth hooks required by the base template but unused in this model.
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        raise ConnectorError("telephony uses the native dialer, not OAuth")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        raise ConnectorError("telephony uses the native dialer, not OAuth")

    def refresh_access_token(self, refresh_token: str) -> dict:
        raise ConnectorError("telephony does not refresh OAuth tokens")

    def revoke(self, access_token: str) -> None:
        raise ConnectorError("telephony does not revoke OAuth tokens")

    # ── prepare path (prepare-tier: shown, never sent) ────────────────────
    def prepare_call(self, recipient: str, purpose: str = "") -> CallBrief:
        """Look up the contact and draft a talking-point brief + script.

        Prepare tier: nothing external happens — the result is shown to the
        user, never dialed. Still permission-gated: an unregistered tool (or
        one sitting at a different tier) is refused here.
        """
        if not recipient or not recipient.strip():
            raise ConnectorError("refusing to prepare a call with no recipient")
        self.require_approval_gate(
            "prepare",
            "telephony.prepare_call",
            {"recipient": recipient, "purpose": purpose},
        )
        contact = self.transport.resolve_contact(recipient)
        talking_points = _draft_talking_points(purpose, contact)
        script = _draft_script(purpose, contact)
        brief = CallBrief(
            recipient=recipient,
            recipient_identity=f"{contact.name} <{contact.phone}>",
            phone=contact.phone,
            purpose=purpose,
            talking_points=talking_points,
            script=script,
        )
        self._audit(
            "system",
            "prepared_call",
            {"recipient": brief.recipient_identity, "purpose": purpose, "dialed": False},
        )
        return brief

    # ── start path (execute-tier, approval-gated, idempotent) ─────────────
    def start_call(
        self,
        recipient: str,
        *,
        script: str = "",
        idempotency_key: str,
        approval_verifier=None,
    ) -> CallHandoff:
        """Open the native dialer pre-filled for `recipient` (v1 handoff).

        No shortcuts: refuses unless the task-runner ledger has an approved
        record for this idempotency_key. Re-playing a key returns the prior
        handoff instead of opening the dialer twice.
        """
        if not recipient or not recipient.strip():
            raise ConnectorError("refusing to start a call with no recipient")

        # Idempotency: a replayed key returns the prior handoff, never re-dials.
        prior = self._vault.load(f"{self.service}_calls", idempotency_key)
        if prior and prior.get("status") == "handed_off":
            return CallHandoff(
                handoff_id=prior["handoff_id"],
                recipient_identity=prior["recipient_identity"],
                phone=prior["phone"],
                channel="native-dialer",
                idempotency_key=idempotency_key,
                replay=True,
            )

        # Resolve the recipient to a canonical identity BEFORE the gate so the
        # diff card shows who will actually be called. Ambiguous names raise.
        contact = self.transport.resolve_contact(recipient)
        recipient_identity = f"{contact.name} <{contact.phone}>"

        self.require_approval_gate(
            "execute",
            "telephony.start_call",
            {
                "recipient": recipient,
                "recipient_identity": recipient_identity,
                "phone": contact.phone,
                "script": script or "no script provided",
                "channel": "native-dialer",
            },
        )
        if approval_verifier is not None and not approval_verifier():
            raise PermissionNotApprovedError(
                "no approved approval exists for this call (task-runner ledger)"
            )

        try:
            handoff_id = self.transport.open_dialer(contact.phone, script)
        except RateLimitError:
            raise
        except ExpiredSessionError:
            raise
        except ConnectorError:
            raise

        self._vault.save(
            f"{self.service}_calls",
            idempotency_key,
            {
                "handoff_id": handoff_id,
                "recipient_identity": recipient_identity,
                "phone": contact.phone,
                "status": "handed_off",
            },
        )
        self._audit(
            "system",
            "started_call_handoff",
            {
                "recipient": recipient_identity,
                "phone": contact.phone,
                "channel": "native-dialer",
                "handoff_id": handoff_id,
                "idempotency_key": idempotency_key,
                "autonomous": False,
            },
        )
        return CallHandoff(
            handoff_id=handoff_id,
            recipient_identity=recipient_identity,
            phone=contact.phone,
            channel="native-dialer",
            idempotency_key=idempotency_key,
        )


# ── deterministic local drafting ($0, no LLM call) ────────────────────────────


def _draft_talking_points(purpose: str, contact: ContactInfo) -> list[str]:
    base = [
        f"Confirm you're speaking with {contact.name}.",
        "State the purpose up front.",
    ]
    if purpose.strip():
        base.append(f"Lead with the purpose: {purpose.strip()}.")
    base.append("Confirm next steps and any deadlines before hanging up.")
    return base


def _draft_script(purpose: str, contact: ContactInfo) -> str:
    lead = purpose.strip() or "to talk through the matter we discussed"
    return (
        f"Hello {contact.name}, this is Vioris calling on behalf of your contact. "
        f"I'm reaching out {lead}. Do you have a moment?"
    )
