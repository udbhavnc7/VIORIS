"""
Unit tests for the Phase 6 Cloud Files connector.

Covers: OAuth lifecycle (connect / refresh / revoke / disconnect), encrypted
storage, read-only metadata scope discipline, rate-limit + expired-session
handling, and the recent-files digest.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    RateLimitError,
)
from integrations.cloud_files.connector import (
    DriveConnector,
    FileDigest,
)
from integrations.mock_providers.drive_mock import MockDriveTransport
from integrations.token_vault import TokenVault


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "drive.db", key=Fernet.generate_key())


@pytest.fixture()
def mock() -> MockDriveTransport:
    return MockDriveTransport()


@pytest.fixture()
def drive(vault: TokenVault, mock: MockDriveTransport) -> DriveConnector:
    return DriveConnector(vault, transport=mock)


def test_connect_stores_and_reports_read_only(drive: DriveConnector) -> None:
    summary = drive.connect("the-code", "http://127.0.0.1:8448/authorized")
    assert summary["account"] == "mock-user@gmail.com"
    assert summary["scopes"] == ["https://www.googleapis.com/auth/drive.metadata.readonly"]


def test_declared_scopes_are_read_only(drive: DriveConnector) -> None:
    scopes = drive.declared_scopes()
    assert "drive.metadata.readonly" in scopes.read[0]
    assert scopes.write == []


def test_connect_missing_identity_fails(drive: DriveConnector) -> None:
    with pytest.raises(ConnectionMissingError):
        drive.fetch_recent_digest("ghost@gmail.com")


def test_digest_returns_recent_files(drive: DriveConnector) -> None:
    drive.connect("c1", "http://127.0.0.1:8448/authorized")
    digest = drive.fetch_recent_digest("mock-user@gmail.com", hours=72)
    assert isinstance(digest, FileDigest)
    assert digest.total == 3
    by_name = {f.name: f for f in digest.files}
    assert "Q3 Budget.xlsx" in by_name
    assert by_name["Q3 Budget.xlsx"].is_folder is False
    assert by_name["Research Notes"].is_folder is True
    assert by_name["Launch Deck.pdf"].owner == "Colleague"
    assert by_name["Launch Deck.pdf"].summary.startswith("Launch Deck.pdf")


def test_rate_limit_propagates_loudly(drive: DriveConnector, mock: MockDriveTransport) -> None:
    drive.connect("c3", "http://127.0.0.1:8448/authorized")
    mock.fail_next = mock.FAIL_RATE_LIMIT
    with pytest.raises(RateLimitError):
        drive.fetch_recent_digest("mock-user@gmail.com")


def test_expired_session_refreshes_once_then_succeeds(
    drive: DriveConnector, mock: MockDriveTransport
) -> None:
    drive.connect("c4", "http://127.0.0.1:8448/authorized")
    mock.fail_next = mock.FAIL_EXPIRED
    digest = drive.fetch_recent_digest("mock-user@gmail.com")
    assert digest.total == 3
    assert "refresh" in mock.calls


def test_disconnect_revokes_and_deletes(drive: DriveConnector, mock: MockDriveTransport) -> None:
    drive.connect("c6", "http://127.0.0.1:8448/authorized")
    drive.revoke_and_disconnect("mock-user@gmail.com")
    assert mock.revoked is True
    assert drive._vault.load("cloud_files", "mock-user@gmail.com") is None