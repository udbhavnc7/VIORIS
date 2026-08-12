"""
Unit tests for the Phase 6 Contacts connector.

Covers: OAuth lifecycle (connect / refresh / revoke / disconnect), encrypted
storage, read-only scope discipline, rate-limit + expired-session handling,
and the contact search/digest with local query filtering.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    RateLimitError,
)
from integrations.contacts.connector import (
    ContactsConnector,
    ContactsDigest,
    _name,
    _summary,
)
from integrations.mock_providers.contacts_mock import MockContactsTransport
from integrations.token_vault import TokenVault


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "contacts.db", key=Fernet.generate_key())


@pytest.fixture()
def mock() -> MockContactsTransport:
    return MockContactsTransport()


@pytest.fixture()
def contacts(vault: TokenVault, mock: MockContactsTransport) -> ContactsConnector:
    return ContactsConnector(vault, transport=mock)


def test_connect_stores_and_reports_read_only(contacts: ContactsConnector) -> None:
    summary = contacts.connect("the-code", "http://127.0.0.1:8450/authorized")
    assert summary["account"] == "mock-user@gmail.com"
    assert summary["scopes"] == ["https://www.googleapis.com/auth/contacts.readonly"]


def test_declared_scopes_are_read_only(contacts: ContactsConnector) -> None:
    scopes = contacts.declared_scopes()
    assert "contacts.readonly" in scopes.read[0]
    assert scopes.write == []


def test_connect_missing_identity_fails(contacts: ContactsConnector) -> None:
    with pytest.raises(ConnectionMissingError):
        contacts.search_contacts("ghost@gmail.com")


def test_search_returns_all_when_no_query(contacts: ContactsConnector) -> None:
    contacts.connect("c1", "http://127.0.0.1:8450/authorized")
    digest = contacts.search_contacts("mock-user@gmail.com")
    assert isinstance(digest, ContactsDigest)
    assert digest.total == 3


def test_search_filters_by_query(contacts: ContactsConnector) -> None:
    contacts.connect("c2", "http://127.0.0.1:8450/authorized")
    digest = contacts.search_contacts("mock-user@gmail.com", query="alice")
    assert digest.total == 1
    assert digest.contacts[0].name == "Alice Chen"
    assert "alice@example.com" in digest.contacts[0].emails


def test_search_matches_phone(contacts: ContactsConnector) -> None:
    contacts.connect("c3", "http://127.0.0.1:8450/authorized")
    digest = contacts.search_contacts("mock-user@gmail.com", query="555-0199")
    assert digest.total == 1
    assert digest.contacts[0].name == "Smile Clinic"


def test_rate_limit_propagates_loudly(contacts: ContactsConnector, mock: MockContactsTransport) -> None:
    contacts.connect("c4", "http://127.0.0.1:8450/authorized")
    mock.fail_next = mock.FAIL_RATE_LIMIT
    with pytest.raises(RateLimitError):
        contacts.search_contacts("mock-user@gmail.com")


def test_expired_session_refreshes_once_then_succeeds(
    contacts: ContactsConnector, mock: MockContactsTransport
) -> None:
    contacts.connect("c5", "http://127.0.0.1:8450/authorized")
    mock.fail_next = mock.FAIL_EXPIRED
    digest = contacts.search_contacts("mock-user@gmail.com")
    assert digest.total == 3
    assert "refresh" in mock.calls


def test_disconnect_revokes_and_deletes(contacts: ContactsConnector, mock: MockContactsTransport) -> None:
    contacts.connect("c6", "http://127.0.0.1:8450/authorized")
    contacts.revoke_and_disconnect("mock-user@gmail.com")
    assert mock.revoked is True
    assert contacts._vault.load("contacts", "mock-user@gmail.com") is None


def test_name_and_summary_helpers() -> None:
    person = {"names": [{"displayName": "Alice Chen"}],
              "emailAddresses": [{"value": "alice@example.com"}],
              "phoneNumbers": [{"value": "+1 555-0100"}]}
    assert _name(person) == "Alice Chen"
    assert "alice@example.com" in _summary(person)
    assert _name({}) == "(unnamed)"