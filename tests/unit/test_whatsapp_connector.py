"""
Unit tests for the Phase 6 WhatsApp connector (Prompt 6.2).

Covers: browser-session link/unlink lifecycle, digest shape (sender / one-line
summary / ask), flagging-instead-of-silently-omitting inaccessible messages,
rate-limit and dead-session handling.
"""

from __future__ import annotations

import pytest

from integrations.base import (
    ConnectionMissingError,
    ConnectorError,
    ExpiredSessionError,
    OAuthScopes,
    PermissionNotApprovedError,
    RateLimitError,
)
from integrations.mock_providers.whatsapp_mock import MockWhatsAppSession
from integrations.token_vault import TokenVault
from integrations.whatsapp.connector import (
    RawMessage,
    WhatsAppConnector,
    WhatsAppDigest,
    _one_line_summary,
    _extract_ask,
    _flag_if_inaccessible,
)

from cryptography.fernet import Fernet


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "wa.db", key=Fernet.generate_key())


@pytest.fixture()
def session() -> MockWhatsAppSession:
    return MockWhatsAppSession()


@pytest.fixture()
def wa(vault: TokenVault, session: MockWhatsAppSession) -> WhatsAppConnector:
    return WhatsAppConnector(vault, transport=session)


# ── lifecycle ────────────────────────────────────────────────────────────────


def test_link_complete_requires_linked_session(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    # linking is persisted only once the provider confirms it can read
    session.complete_scan("sess-abc")
    ok = wa.complete_link("sess-abc")
    assert ok["connector"] == "whatsapp"
    assert ok["account"] == "sess-abc"
    assert wa._vault.require_entry("whatsapp", "sess-abc") is not None


def test_unlink_drops_vault_and_marks_revoked(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    session.complete_scan("sess-xyz")
    wa.complete_link("sess-xyz")
    wa.unlink_session("sess-xyz")
    assert "unlink" in session.calls
    assert wa._vault.load("whatsapp", "sess-xyz") is None


# ── digest shape ─────────────────────────────────────────────────────────────


def test_digest_shape_has_sender_summary_ask(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    session.complete_scan("sess-1")
    wa.complete_link("sess-1")
    digest = wa.fetch_digest("sess-1", hours=24)

    assert isinstance(digest, WhatsAppDigest)
    by_sender = {e.sender: e for e in digest.entries}

    mum = by_sender["Mum"]
    assert mum.summary  # one-line summary present
    assert "photos" in mum.summary  # content preserved, pronouns stripped
    assert mum.inaccessible_reason is None

    recruiter_adj = by_sender["Dan (florist)"]
    assert recruiter_adj.ask  # 'asking about' surfaced
    assert recruiter_adj.contains_request_words is True


def test_digest_flags_not_silently_omits(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    session.complete_scan("sess-2")
    wa.complete_link("sess-2")
    digest = wa.fetch_digest("sess-2", hours=24)
    # the view-once media message is present and flagged, not dropped
    flagged = [e for e in digest.entries if e.inaccessible_reason]
    assert any("view-once" in e.inaccessible_reason for e in flagged)
    assert digest.flagged_hidden >= 1
    assert digest.total == len(digest.entries)  # nothing silently omitted


def test_scopes_and_tools_are_separated(wa: WhatsAppConnector) -> None:
    scopes: OAuthScopes = wa.declared_scopes()
    assert scopes.read == ["browser-session:read-messages"]
    assert scopes.write == ["browser-session:send-messages"]
    assert "whatsapp.read_digest" in wa.tools
    assert "whatsapp.send_message" in wa.tools


# ── send path (execute-tier, approval-gated, idempotent) ─────────────────────


def test_send_requires_approved_ledger(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    from packages.shared.permission_engine import PermissionEngine
    from packages.shared.schemas import ToolRegistration, RiskTier

    PermissionEngine.reset()
    PermissionEngine.register(
        ToolRegistration(
            tool_name="whatsapp.send_message",
            tier=RiskTier.EXECUTE,
            confirmation_required=True,
            description="send",
            diff_card_fields=["recipient", "recipient_identity", "content", "channel"],
        )
    )
    session.complete_scan("sess-send")
    wa.complete_link("sess-send")
    with pytest.raises(PermissionNotApprovedError):
        wa.send_message(
            "sess-send",
            recipient="Mum",
            content="On my way!",
            idempotency_key="key-1",
            approval_verifier=lambda: False,
        )
    assert session.sent == []  # nothing transmitted without approval


def test_send_fires_with_approved_ledger(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    from packages.shared.permission_engine import PermissionEngine
    from packages.shared.schemas import ToolRegistration, RiskTier

    PermissionEngine.reset()
    PermissionEngine.register(
        ToolRegistration(
            tool_name="whatsapp.send_message",
            tier=RiskTier.EXECUTE,
            confirmation_required=True,
            description="send",
            diff_card_fields=["recipient", "recipient_identity", "content", "channel"],
        )
    )
    session.complete_scan("sess-send2")
    wa.complete_link("sess-send2")
    sent = wa.send_message(
        "sess-send2",
        recipient="Mum",
        content="On my way!",
        idempotency_key="key-2",
        approval_verifier=lambda: True,
    )
    assert sent.replay is False
    assert sent.recipient_identity == "Mum <+447700900001>"  # resolved, not typed
    assert sent.channel == "whatsapp"
    assert session.sent[0]["recipient_identity"] == "Mum <+447700900001>"
    assert sent.message_id == "sent_1"


def test_send_idempotency_key_replays_prior_result(
    wa: WhatsAppConnector, session: MockWhatsAppSession
) -> None:
    from packages.shared.permission_engine import PermissionEngine
    from packages.shared.schemas import ToolRegistration, RiskTier

    PermissionEngine.reset()
    PermissionEngine.register(
        ToolRegistration(
            tool_name="whatsapp.send_message",
            tier=RiskTier.EXECUTE,
            confirmation_required=True,
            description="send",
            diff_card_fields=["recipient", "recipient_identity", "content", "channel"],
        )
    )
    session.complete_scan("sess-send3")
    wa.complete_link("sess-send3")
    first = wa.send_message(
        "sess-send3",
        recipient="Mum",
        content="On my way!",
        idempotency_key="key-3",
        approval_verifier=lambda: True,
    )
    second = wa.send_message(
        "sess-send3",
        recipient="Mum",
        content="On my way!",
        idempotency_key="key-3",
        approval_verifier=lambda: True,
    )
    assert second.replay is True
    assert second.message_id == first.message_id
    assert len(session.sent) == 1  # duplicate retry never double-sends


def test_send_ambiguous_recipient_is_loud(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    from packages.shared.permission_engine import PermissionEngine
    from packages.shared.schemas import ToolRegistration, RiskTier

    PermissionEngine.reset()
    PermissionEngine.register(
        ToolRegistration(
            tool_name="whatsapp.send_message",
            tier=RiskTier.EXECUTE,
            confirmation_required=True,
            description="send",
            diff_card_fields=["recipient", "recipient_identity", "content", "channel"],
        )
    )
    session.complete_scan("sess-send4")
    wa.complete_link("sess-send4")
    with pytest.raises(ConnectorError, match="ambiguous"):
        wa.send_message(
            "sess-send4",
            recipient="Alex",
            content="Hi",
            idempotency_key="key-4",
            approval_verifier=lambda: True,
        )
    assert session.sent == []


def test_send_refuses_empty_content(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    session.complete_scan("sess-send5")
    wa.complete_link("sess-send5")
    with pytest.raises(ConnectorError, match="empty message"):
        wa.send_message(
            "sess-send5",
            recipient="Mum",
            content="   ",
            idempotency_key="key-5",
            approval_verifier=lambda: True,
        )


def test_send_unlinked_session_is_loud(wa: WhatsAppConnector) -> None:
    with pytest.raises(ConnectionMissingError):
        wa.send_message(
            "never-linked",
            recipient="Mum",
            content="Hi",
            idempotency_key="key-6",
            approval_verifier=lambda: True,
        )


# ── failure handling ─────────────────────────────────────────────────────────


def test_rate_limit_propagates_loudly(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    session.complete_scan("sess-3")
    wa.complete_link("sess-3")
    session.fail_next = session.FAIL_RATE_LIMIT
    with pytest.raises(RateLimitError):
        wa.fetch_digest("sess-3")


def test_dead_session_is_expired(wa: WhatsAppConnector, session: MockWhatsAppSession) -> None:
    session.complete_scan("sess-4")
    wa.complete_link("sess-4")
    session.linked_sessions.discard("sess-4")  # provider dropkick (401)
    with pytest.raises(ExpiredSessionError):
        wa.fetch_digest("sess-4")
    assert wa._vault.load("whatsapp", "sess-4")["expired"] is True


def test_unlinked_fetch_refuses_early(wa: WhatsAppConnector) -> None:
    # never-linked session has no vault entry: refuse with connection error
    from integrations.base import ConnectionMissingError

    with pytest.raises(ConnectionMissingError):
        wa.fetch_digest("never-linked")

def _raw(text: str, kind: str = "text", inaccessible_reason: str | None = None) -> RawMessage:
    return RawMessage(message_id="x", sender="S", text=text, kind=kind,
                      inaccessible_reason=inaccessible_reason)


# ── heuristics ───────────────────────────────────────────────────────────────


def test_one_line_summary_strips_pronouns_and_caps() -> None:
    s = _one_line_summary("I need the report by tomorrow, please send it to me now it's urgent.")
    assert "I" not in s and "me" not in s
    assert len(s) > 0


def test_extract_ask_detects_requests_and_questions() -> None:
    assert _extract_ask("Asking about the bouquet delivery")[0] == "asking about"
    assert _extract_ask("requesting your student information")[0] == "requesting"
    assert _extract_ask("When is the deadline?")[0] == "asking timing"
    assert _extract_ask("Just saying hi")[0] is None


def test_flag_if_inaccessible_zeroes_summary_and_ask() -> None:
    entry = _flag_if_inaccessible(_raw("[view once]", inaccessible_reason="view-once"))
    assert entry.inaccessible_reason == "view-once"
    assert entry.summary == "[not accessible]"
    assert entry.ask is None