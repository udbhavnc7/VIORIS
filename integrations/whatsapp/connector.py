"""
WhatsApp connector — browser-session based, DIGEST ONLY (Phase 6, Prompt 6.2).

Approach: WhatsApp Business API requires a paid Meta business tier, so per the
$0 constraint and the Phase 0 research question ("browser-session approach"),
this connector drives WhatsApp Web through a local browser session. It is
READ-ONLY: it produces a digest and nothing else. There is no send path and
no write scope; anything future must go through the permission engine as
Execute and reuse none of this read path.

Digest shape (Prompt 6.2, exact fields):
    sender          -> sender name
    summary         -> one-line summary of the message
    ask             -> explicit ask, e.g. "asking about flowers"
    + inaccessible  -> flagged, NOT silently omitted

Anything the browser session cannot reach (undecryptable, restricted view,
deleted/expiring message, media without caption) is included with
`inaccessible_reason` set — never dropped. The transport claims a read-only
view; it defines WHAT is readable and what is not.

The token vault stores the encrypted browser-session reference; tokens are
never exposed. Failure handling mirrors the base template: rate limits and
dead sessions are explicit and loud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    ExpiredSessionError,
    OAuthScopes,
    RateLimitError,
)

PERMISSION_EXPLANATION = (
    "Read only recent WhatsApp conversations from your linked browser session: "
    "sender names, one-line summaries, and any explicit ask. It cannot send, "
    "delete, or change anything in your account."
)

#: In the browser-session model there are no OAuth scopes; the connector still
#: declares a read-only capability surface so the template's gate stays intact.
_SESSION_SCOPE = "browser-session:read-messages"


@dataclass
class RawMessage:
    """What the transport observed from the browser session."""

    message_id: str
    sender: str
    text: str
    kind: str = "text"  # text | media | system
    inaccessible_reason: str | None = None  # set -> NOT silently omitted
    timestamp: datetime | None = None


@dataclass
class DigestEntry:
    sender: str
    summary: str  # one-line summary
    ask: str | None  # explicit ask, or None
    message_id: str
    inaccessible_reason: str | None = None
    kind: str = "text"
    contains_request_words: bool = False


@dataclass
class WhatsAppDigest:
    account: str
    window_hours: int
    total: int
    flagged_hidden: int  # how many were reported as inaccessible (shown, not dropped)
    entries: list[DigestEntry] = field(default_factory=list)


class WhatsAppTransport(Protocol):
    """Minimal browser-session surface a WhatsApp connector needs."""

    def is_linked(self, session_ref: str) -> bool: ...

    def fetch_recent(self, session_ref: str, *, hours: int, max_messages: int) -> list[RawMessage]: ...

    def link(self) -> dict:
        """Start linking a new device (returns QR/polling handle + session ref)."""

    def unlink(self, session_ref: str) -> None: ...


# ── one-line summary + ask heuristics (local, deterministic, $0) ────────────

_PRONOUN_KILL = re.compile(r"\b(i|me|my|we|us|our)\b", re.IGNORECASE)


def _one_line_summary(text: str) -> str:
    """A defensible one-line summary without an LLM: strip pinning pronouns,
    collapse whitespace, cap at ~160 chars, and mark truncation."""
    if not text:
        return "(empty message)"
    cleaned = _PRONOUN_KILL.sub("", text)
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= 160:
        return cleaned[:160]
    return cleaned[:157].rstrip() + "…"


_ASK_PATTERNS = [
    (r"\b(asking|wondering|wanting)\s+(about|for)\b", "asking about"),
    (r"\b(request(ing|ed|s)?)\b", "requesting"),
    (r"\b(need|needs|could use)\b", "needs"),
    (r"\b(can|could|will)\s+you\b", "asking"),
    (r"\bplease\s+(send|share|check|confirm|upload|prepare)\b", "requesting"),
    (r"\bwhen\s+(is|are|can)\b", "asking timing"),
    (r"\btomorrow|tonight|today|by monday|by friday|eod\b", "has deadline"),
]


def _extract_ask(text: str) -> tuple[str | None, bool]:
    """Return (ask descriptor, contains_request_words). Deterministic."""
    lower = text.lower()
    for pattern, label in _ASK_PATTERNS:
        if re.search(pattern, lower):
            return label, True
    if "?" in text:
        return "asking a question", True
    return None, False


def _flag_if_inaccessible(raw: RawMessage) -> DigestEntry:
    """Build the digest entry, carrying the accessibility flag through."""
    ask, has_request = _extract_ask(raw.text)
    return DigestEntry(
        sender=raw.sender,
        summary=_one_line_summary(raw.text) if raw.inaccessible_reason is None else "[not accessible]",
        ask=ask if raw.inaccessible_reason is None else None,
        message_id=raw.message_id,
        inaccessible_reason=raw.inaccessible_reason,
        kind=raw.kind,
        contains_request_words=has_request,
    )


# ── connector ────────────────────────────────────────────────────────────────


class WhatsAppConnector(AbstractConnector):
    """Browser-session WhatsApp digest connector. Tools: whatsapp.read_digest."""

    service = "whatsapp"
    auth_url = ""  # browser-session model: no OAuth endpoints
    token_url = ""
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["whatsapp.read_digest"]

    def __init__(self, vault, transport: WhatsAppTransport | None = None, **_ignored) -> None:
        super().__init__(vault, "")
        self.transport = transport

    def declared_scopes(self) -> OAuthScopes:
        # Browser-session: no OAuth scopes; capabilities enforced by transport.
        return OAuthScopes(read=[_SESSION_SCOPE], write=[])

    # OAuth hooks required by the base template but unused in this model.
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        raise ConnectorError("whatsapp uses browser-session linking, not OAuth")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        raise ConnectorError("whatsapp uses browser-session linking, not OAuth")

    def refresh_access_token(self, refresh_token: str) -> dict:
        raise ConnectorError("browser-session sessions are re-linked, not refreshed via OAuth")

    def revoke(self, access_token: str) -> None:
        raise ConnectorError("browser-session sessions are unlinked, not revoked")

    # ── session lifecycle (vault-backed) ───────────────────────────────────
    def link_device(self) -> dict:
        """Begin device linking. Real transport returns a QR/polling handle;
        the session reference is stored encrypted on completion."""
        handle = self.transport.link()
        # The handle (e.g. QR state) is transient and not secret-bearing by itself;
        # the caller stores the encrypted session reference once scanning finishes.
        return handle

    def complete_link(self, session_ref: str) -> dict:
        """The user scanned/paired; persist the session reference encrypted."""
        if not self.transport.is_linked(session_ref):
            raise ConnectorError("browser session not linked on the provider side")
        self._vault.save(self.service, session_ref, {"session_ref": session_ref},
                         scopes=[_SESSION_SCOPE])
        self._audit(session_ref, "linked", {"connector": self.service})
        return {"connector": self.service, "account": session_ref,
                "scopes": self._scopes.read}

    def unlink_session(self, session_ref: str) -> None:
        self._vault.require_entry(self.service, session_ref)
        self.transport.unlink(session_ref)
        self._vault.delete(self.service, session_ref)
        self._audit(session_ref, "unlinked", {"connector": self.service})

    # ── digest path (observe-tier, transparent) ────────────────────────────
    def fetch_digest(self, session_ref: str, hours: int = 24, max_messages: int = 40) -> WhatsAppDigest:
        entry = self._vault.require_entry(self.service, session_ref)
        if entry.get("expired"):
            raise ExpiredSessionError(f"{session_ref} session expired — re-link required")

        try:
            raws = self.transport.fetch_recent(
                session_ref, hours=hours, max_messages=max_messages
            )
        except RateLimitError:
            raise
        except ExpiredSessionError:
            self._vault.mark_expired(self.service, session_ref)
            raise
        except ConnectorError as exc:
            # provider-view failure is flagged, not silent
            raise RateLimitError(f"whatsapp fetch failed loudly: {exc}") from exc

        entries = [_flag_if_inaccessible(m) for m in raws]
        flagged = len([e for e in entries if e.inaccessible_reason])
        return WhatsAppDigest(
            account=session_ref,
            window_hours=hours,
            total=len(raws),
            flagged_hidden=flagged,
            entries=entries,
        )


class PlaywrightWhatsAppTransport:
    """Real browser-session transport driving WhatsApp Web via Playwright.

    Kept lazy so the connector imports with zero deps; the production path
    manages a persistent Chromium profile so the session survives restarts.
    """

    def __init__(self, *, channel: str = "chromium", user_data_dir: str | None = None) -> None:
        import playwright  # noqa: F401 - eager import fails in test env w/o install
        self._channel = channel
        self._user_data_dir = user_data_dir

    def link(self) -> dict:
        raise ConnectorError(
            "playwright transport requires provisioning a persistent profile and "
            "a QR-link flow; use MockWhatsAppSession in tests"
        )

    def is_linked(self, session_ref: str) -> bool:
        raise ConnectorError("link status check requires an active playwright session")

    def fetch_recent(self, session_ref, *, hours, max_messages) -> list[RawMessage]:
        raise ConnectorError("fetch_recent requires an active playwright session")

    def unlink(self, session_ref) -> None:
        raise ConnectorError("unlink requires an active playwright session")