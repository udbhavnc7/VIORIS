"""
Unit tests for the Phase 6 Notes connector.

Covers: OAuth lifecycle (connect / refresh / revoke / disconnect), encrypted
storage, read-only scope discipline, rate-limit + expired-session handling,
and the recent-notes digest including body extraction.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    RateLimitError,
)
from integrations.mock_providers.notes_mock import MockNotesTransport
from integrations.notes.connector import (
    NotesConnector,
    NotesDigest,
    _body_text,
    _summary,
)
from integrations.token_vault import TokenVault


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "notes.db", key=Fernet.generate_key())


@pytest.fixture()
def mock() -> MockNotesTransport:
    return MockNotesTransport()


@pytest.fixture()
def notes(vault: TokenVault, mock: MockNotesTransport) -> NotesConnector:
    return NotesConnector(vault, transport=mock)


def test_connect_stores_and_reports_read_only(notes: NotesConnector) -> None:
    summary = notes.connect("the-code", "http://127.0.0.1:8449/authorized")
    assert summary["account"] == "mock-user@gmail.com"
    assert summary["scopes"] == ["https://www.googleapis.com/auth/documents.readonly"]


def test_declared_scopes_are_read_only(notes: NotesConnector) -> None:
    scopes = notes.declared_scopes()
    assert "documents.readonly" in scopes.read[0]
    assert scopes.write == []


def test_connect_missing_identity_fails(notes: NotesConnector) -> None:
    with pytest.raises(ConnectionMissingError):
        notes.fetch_recent_digest("ghost@gmail.com")


def test_digest_returns_notes_with_bodies(notes: NotesConnector) -> None:
    notes.connect("c1", "http://127.0.0.1:8449/authorized")
    digest = notes.fetch_recent_digest("mock-user@gmail.com", hours=168)
    assert isinstance(digest, NotesDigest)
    assert digest.total == 3
    by_title = {n.title: n for n in digest.notes}
    assert "Grocery list" in by_title
    assert "Eggs for the weekend" in by_title["Grocery list"].body
    assert "Milk" in by_title["Grocery list"].summary
    assert "Passwords (do not share)" in by_title


def test_rate_limit_propagates_loudly(notes: NotesConnector, mock: MockNotesTransport) -> None:
    notes.connect("c3", "http://127.0.0.1:8449/authorized")
    mock.fail_next = mock.FAIL_RATE_LIMIT
    with pytest.raises(RateLimitError):
        notes.fetch_recent_digest("mock-user@gmail.com")


def test_expired_session_refreshes_once_then_succeeds(
    notes: NotesConnector, mock: MockNotesTransport
) -> None:
    notes.connect("c4", "http://127.0.0.1:8449/authorized")
    mock.fail_next = mock.FAIL_EXPIRED
    digest = notes.fetch_recent_digest("mock-user@gmail.com")
    assert digest.total == 3
    assert "refresh" in mock.calls


def test_disconnect_revokes_and_deletes(notes: NotesConnector, mock: MockNotesTransport) -> None:
    notes.connect("c6", "http://127.0.0.1:8449/authorized")
    notes.revoke_and_disconnect("mock-user@gmail.com")
    assert mock.revoked is True
    assert notes._vault.load("notes", "mock-user@gmail.com") is None


def test_body_text_and_summary_helpers() -> None:
    doc = {"body": {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": "Line one\n"}}]}},
        {"paragraph": {"elements": [{"textRun": {"content": "Line two"}}]}},
    ]}}
    assert _body_text(doc) == "Line one\nLine two"
    assert _summary("A title", "short body") == "short body"
    assert _summary("A title", "x" * 300).endswith("…")