"""
Unit tests for the Gmail write connector (reactions + replies).

Verifies the deliberate split from the read-only connector: write scopes live
only here, both tools are Execute-tier, an unapproved call never touches the
transport, and replayed idempotency keys do not double-apply or double-send.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from integrations.base import ConnectorError, PermissionNotApprovedError
from integrations.gmail.connector import SCOPE_READONLY
from integrations.gmail.write_connector import (
    SCOPE_MODIFY,
    SCOPE_SEND,
    GmailWriteConnector,
    build_raw_message,
)
from integrations.mock_providers.gmail_mock import MockGmailTransport
from integrations.token_vault import TokenVault
from packages.shared.permission_engine import (
    PermissionEngine,
    register_all_tools,
)
from packages.shared.schemas import RiskTier


@pytest.fixture(autouse=True)
def _registry():
    """Deterministic, full registry: other modules freeze/reset it."""
    PermissionEngine.reset()
    register_all_tools()
    yield
    PermissionEngine.reset()
    register_all_tools()


@pytest.fixture()
def vault(tmp_path: Path) -> TokenVault:
    return TokenVault(tmp_path / "tokens.db", key=Fernet.generate_key())


@pytest.fixture()
def mock() -> MockGmailTransport:
    return MockGmailTransport()


@pytest.fixture()
def gmail_write(vault: TokenVault, mock: MockGmailTransport) -> GmailWriteConnector:
    return GmailWriteConnector(vault, transport=mock)


def _connect(connector: GmailWriteConnector, mock: MockGmailTransport) -> str:
    mock._auth_scope = " ".join([SCOPE_READONLY, SCOPE_MODIFY, SCOPE_SEND])
    summary = connector.connect("code-write", "http://127.0.0.1:8445/authorized")
    return summary["account"]


# ── registry / scope discipline ──────────────────────────────────────────────


def test_write_connector_declares_separate_write_scopes(
    gmail_write: GmailWriteConnector,
) -> None:
    scopes = gmail_write.declared_scopes()
    assert scopes.read == [SCOPE_READONLY]
    assert scopes.write == [SCOPE_MODIFY, SCOPE_SEND]


def test_react_and_reply_are_execute_tier() -> None:
    assert PermissionEngine.classify("gmail.react").tier == RiskTier.EXECUTE
    assert PermissionEngine.classify("gmail.send_reply").tier == RiskTier.EXECUTE
    assert PermissionEngine.classify("gmail.read_unread").tier == RiskTier.OBSERVE


# ── reactions ────────────────────────────────────────────────────────────────


def test_react_refused_without_approval(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    with pytest.raises(PermissionNotApprovedError):
        gmail_write.react(
            identity,
            message_id="m1",
            reaction="acknowledge",
            idempotency_key="k-react-1",
            approval_verifier=lambda: False,
        )
    assert mock.modified == []  # nothing touched the mailbox


def test_acknowledge_stars_and_marks_read(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    result = gmail_write.react(
        identity,
        message_id="m1",
        reaction="acknowledge",
        idempotency_key="k-react-2",
        approval_verifier=lambda: True,
    )
    assert result.replay is False
    assert result.added_labels == ["STARRED"]
    assert result.removed_labels == ["UNREAD"]
    assert mock.modified[0]["label_ids"] == ["INBOX", "STARRED"]
    # the read-only connector's scope was never widened
    assert gmail_write.declared_scopes().write == [SCOPE_MODIFY, SCOPE_SEND]


def test_react_label_requires_a_label_id(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    with pytest.raises(ConnectorError, match="label id"):
        gmail_write.react(
            identity,
            message_id="m1",
            reaction="label",
            idempotency_key="k-react-3",
            approval_verifier=lambda: True,
        )
    assert mock.modified == []


def test_react_rejects_unknown_reaction(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    with pytest.raises(ConnectorError, match="unknown reaction"):
        gmail_write.react(
            identity,
            message_id="m1",
            reaction="party",
            idempotency_key="k-react-4",
            approval_verifier=lambda: True,
        )


def test_react_replay_does_not_touch_the_mailbox_twice(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    first = gmail_write.react(
        identity,
        message_id="m1",
        reaction="star",
        idempotency_key="k-react-5",
        approval_verifier=lambda: True,
    )
    second = gmail_write.react(
        identity,
        message_id="m1",
        reaction="star",
        idempotency_key="k-react-5",
        approval_verifier=lambda: True,
    )
    assert first.replay is False
    assert second.replay is True
    assert len(mock.modified) == 1


# ── replies ──────────────────────────────────────────────────────────────────


def test_reply_refused_without_approval(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    with pytest.raises(PermissionNotApprovedError):
        gmail_write.send_reply(
            identity,
            thread_id="t1",
            to="boss@example.com",
            subject="Re: Interview scheduling",
            body="Tuesday works.",
            idempotency_key="k-reply-1",
            approval_verifier=lambda: False,
        )
    assert mock.sent == []


def test_reply_sends_once_and_is_idempotent(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    first = gmail_write.send_reply(
        identity,
        thread_id="t1",
        to="boss@example.com",
        subject="Re: Interview scheduling",
        body="Tuesday works.",
        idempotency_key="k-reply-2",
        approval_verifier=lambda: True,
    )
    assert first.replay is False
    assert first.message_id == "sent_1"
    assert first.thread_id == "t1"
    assert len(mock.sent) == 1

    decoded = base64.urlsafe_b64decode(mock.sent[0]["raw"]).decode("utf-8")
    assert "To: boss@example.com" in decoded
    assert "Subject: Re: Interview scheduling" in decoded
    assert "Tuesday works." in decoded

    second = gmail_write.send_reply(
        identity,
        thread_id="t1",
        to="boss@example.com",
        subject="Re: Interview scheduling",
        body="Tuesday works.",
        idempotency_key="k-reply-2",
        approval_verifier=lambda: True,
    )
    assert second.replay is True
    assert second.message_id == first.message_id
    assert len(mock.sent) == 1  # a retry never double-sends


def test_reply_refuses_header_injection(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    with pytest.raises(ConnectorError, match="line breaks"):
        gmail_write.send_reply(
            identity,
            thread_id="t1",
            to="boss@example.com\nBcc: attacker@example.com",
            subject="Re: Interview scheduling",
            body="Tuesday works.",
            idempotency_key="k-reply-3",
            approval_verifier=lambda: True,
        )
    assert mock.sent == []


def test_reply_refuses_empty_body(
    gmail_write: GmailWriteConnector, mock: MockGmailTransport
) -> None:
    identity = _connect(gmail_write, mock)
    with pytest.raises(ConnectorError, match="empty reply"):
        gmail_write.send_reply(
            identity,
            thread_id="t1",
            to="boss@example.com",
            subject="Re: Interview scheduling",
            body="   ",
            idempotency_key="k-reply-4",
            approval_verifier=lambda: True,
        )


def test_build_raw_message_is_base64url_rfc822() -> None:
    raw = build_raw_message(to="a@example.com", subject="Hi", body="Hello")
    decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
    assert decoded.startswith("To: a@example.com")
    assert "Subject: Hi" in decoded
    assert "Hello" in decoded
