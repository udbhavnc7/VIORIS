"""
Unit tests for the Phase 6 WhatsApp connector (Prompt 6.2).

Covers: browser-session link/unlink lifecycle, digest shape (sender / one-line
summary / ask), flagging-instead-of-silently-omitting inaccessible messages,
rate-limit and dead-session handling.
"""

from __future__ import annotations

import pytest

from integrations.base import (
    ExpiredSessionError,
    OAuthScopes,
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


def test_digest_is_read_only_no_send_path(wa: WhatsAppConnector) -> None:
    scopes: OAuthScopes = wa.declared_scopes()
    assert scopes.write == []
    assert "whatsapp.read_digest" in wa.tools
    assert not any("send" in t for t in wa.tools)


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