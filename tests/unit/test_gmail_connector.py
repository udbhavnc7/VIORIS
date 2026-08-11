"""
Unit tests for the Phase 6 connector base, token vault, and Gmail connector.

Covers: OAuth lifecycle (connect / refresh / revoke / disconnect), encrypted
storage, read-only scope discipline, rate-limit + expired-session handling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    ExpiredSessionError,
    RateLimitError,
)
from integrations.gmail.connector import GmailConnector, MailDigest
from integrations.gmail.connector import _urgency, _extract_asks, _extract_dates
from integrations.mock_providers.gmail_mock import MockGmailTransport
from integrations.token_vault import TokenVault


@pytest.fixture()
def vault(tmp_path: Path) -> TokenVault:
    return TokenVault(tmp_path / "tokens.db", key=Fernet.generate_key())


@pytest.fixture()
def mock() -> MockGmailTransport:
    return MockGmailTransport()


@pytest.fixture()
def gmail(vault: TokenVault, mock: MockGmailTransport) -> GmailConnector:
    return GmailConnector(vault, transport=mock)


# ── token vault ──────────────────────────────────────────────────────────────


def test_vault_roundtrip_encrypted(vault: TokenVault) -> None:
    vault.save("gmail", "me@gmail.com", {"access_token": "abc", "refresh_token": "rt"})
    entry = vault.load("gmail", "me@gmail.com")
    assert entry["access_token"] == "abc"
    assert entry["refresh_token"] == "rt"
    # never exposed via list()
    mids = vault.list("gmail")
    assert mids[0]["account"] == "me@gmail.com"
    assert "access_token" not in mids[0]


def test_vault_plaintext_ciphertext_differ(vault: TokenVault, tmp_path: Path) -> None:
    vault.save("gmail", "a", {"access_token": "secret-value"})
    blob = (tmp_path / "tokens.db").read_text("utf-8", errors="replace")
    assert "secret-value" not in blob


def test_vault_delete_and_missing(vault: TokenVault) -> None:
    vault.save("gmail", "a", {"access_token": "x"})
    vault.delete("gmail", "a")
    assert vault.load("gmail", "a") is None
    with pytest.raises(ConnectionMissingError):
        vault.require_entry("gmail", "a")


# ── connector lifecycle ──────────────────────────────────────────────────────


def test_connect_stores_and_reports_read_only(gmail: GmailConnector) -> None:
    summary = gmail.connect("the-code", "http://127.0.0.1:8445/authorized")
    assert summary["account"] == "mock-user@gmail.com"
    assert summary["scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_declared_scopes_are_read_only(gmail: GmailConnector) -> None:
    scopes = gmail.declared_scopes()
    assert scopes.read == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert scopes.write == []  # READ-ONLY: never a write scope


def test_connect_missing_identity_fails(gmail: GmailConnector) -> None:
    with pytest.raises(ConnectionMissingError):
        gmail.fetch_unread_digest("ghost@gmail.com")


# ── digest path ──────────────────────────────────────────────────────────────


def test_digest_returns_urgencies_asks_and_dates(gmail: GmailConnector) -> None:
    gmail.connect("c1", "http://127.0.0.1:8445/authorized")
    digest = gmail.fetch_unread_digest("mock-user@gmail.com", hours=24)
    assert isinstance(digest, MailDigest)
    assert digest.total_unread == 3
    by_subject = {i.subject: i for i in digest.items}
    urgent = by_subject["URGENT: action required on the launch"]
    assert urgent.urgency == "high"
    assert any("confirm" in a for a in urgent.asks)
    assert urgent.sender == "Boss <boss@example.com>"
    # the recruiter message carries an explicit ask
    recruiter = by_subject["Interview scheduling"]
    assert any("let me know" in a for a in recruiter.asks)


def test_digest_honors_max_results(gmail: GmailConnector) -> None:
    gmail.connect("c2", "http://127.0.0.1:8445/authorized")
    digest = gmail.fetch_unread_digest("mock-user@gmail.com", max_results=1)
    assert len(digest.items) == 1
    assert digest.truncated is True  # mock reports a nextPageToken


def test_rate_limit_propagates_loudly(gmail: GmailConnector, mock: MockGmailTransport) -> None:
    gmail.connect("c3", "http://127.0.0.1:8445/authorized")
    mock.fail_next = mock.FAIL_RATE_LIMIT
    with pytest.raises(RateLimitError):
        gmail.fetch_unread_digest("mock-user@gmail.com")
    # no silent loop: exactly one list call happened
    assert [c for c in mock.calls if c.startswith("list_unread")].__len__() == 1


def test_expired_session_refreshes_once_then_succeeds(
    gmail: GmailConnector, mock: MockGmailTransport
) -> None:
    gmail.connect("c4", "http://127.0.0.1:8445/authorized")
    mock.fail_next = mock.FAIL_EXPIRED  # first list call 401s
    digest = gmail.fetch_unread_digest("mock-user@gmail.com")
    assert digest.total_unread == 3
    assert "refresh" in mock.calls  # transparent single refresh happened


def test_expired_without_refresh_token_is_loud(
    gmail: GmailConnector, mock: MockGmailTransport
) -> None:
    # simulate a session whose access is dead and whose refresh token was lost
    gmail.connect("c5", "http://127.0.0.1:8445/authorized")
    entry = gmail._vault.load("gmail", "mock-user@gmail.com")
    entry.pop("refresh_token")
    gmail._vault.save("gmail", "mock-user@gmail.com", entry)
    mock.fail_next = mock.FAIL_EXPIRED  # access token rejected on first call
    with pytest.raises(ExpiredSessionError):
        gmail.fetch_unread_digest("mock-user@gmail.com")


def test_disconnect_revokes_and_deletes(gmail: GmailConnector, mock: MockGmailTransport) -> None:
    gmail.connect("c6", "http://127.0.0.1:8445/authorized")
    gmail.revoke_and_disconnect("mock-user@gmail.com")
    assert mock.revoked is True
    assert gmail._vault.load("gmail", "mock-user@gmail.com") is None


# ── heuristics ───────────────────────────────────────────────────────────────


def test_urgency_scores() -> None:
    assert _urgency("URGENT: please confirm", "asap") == "high"
    assert _urgency("Weekly digest", "here are stories") == "low"
    assert _urgency("Reminder: review by tomorrow", "due EOD") == "medium"


def test_asks_and_dates_extraction() -> None:
    asks = _extract_asks("Can you confirm", "please review by Friday")
    assert any("confirm" in a for a in asks)
    dates = _extract_dates("", "reply by Friday please, or tomorrow")
    assert "tomorrow" in dates
    assert "friday" in dates