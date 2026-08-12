"""
Unit tests for the Phase 6 History connector.

Covers: local-snapshot lifecycle (connect / disconnect), encrypted vault,
read-only capability surface, hours window filtering, and loud unreadable-file
handling. Also exercises the real LocalHistoryTransport against a temp
Chromium-shaped SQLite file (read-only URI, no writes, no network).
"""

from __future__ import annotations

import sqlite3

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    ConnectorError,
)
from integrations.history.connector import (
    HistoryConnector,
    HistoryDigest,
    LocalHistoryTransport,
    _dt_from_chromium,
    _summary,
)
from integrations.mock_providers.history_mock import MockHistoryTransport
from integrations.token_vault import TokenVault


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "hist.db", key=Fernet.generate_key())


@pytest.fixture()
def mock() -> MockHistoryTransport:
    return MockHistoryTransport()


@pytest.fixture()
def hc(vault: TokenVault, mock: MockHistoryTransport, tmp_path) -> HistoryConnector:
    # connect_snapshot validates the source exists on disk (real safety), so
    # give the mock-based tests a real (dummy) file to point at.
    src = tmp_path / "History"
    src.write_text("", encoding="utf-8")
    _hc = HistoryConnector(vault, transport=mock)
    _hc._source = str(src)
    return _hc


def _source(hc: HistoryConnector) -> str:
    return hc._source


def test_no_oauth_scopes_declared(hc: HistoryConnector) -> None:
    scopes = hc.declared_scopes()
    assert scopes.write == []
    assert "browser-profile:read-history" in scopes.read
    assert "history.recent" in hc.tools


def test_connect_validates_file_exists(vault: TokenVault, tmp_path) -> None:
    hc = HistoryConnector(vault, transport=MockHistoryTransport())
    with pytest.raises(ConnectorError, match="not found"):
        hc.connect_snapshot(str(tmp_path / "nope"))


def test_connect_and_recent_lifecycle(hc: HistoryConnector) -> None:
    hc.connect_snapshot(_source(hc))
    digest = hc.recent_history(_source(hc))
    assert isinstance(digest, HistoryDigest)
    assert digest.total == 2  # default 72h window excludes the 200h-old row


def test_hours_window_excludes_old(hc: HistoryConnector) -> None:
    hc.connect_snapshot(_source(hc))
    digest = hc.recent_history(_source(hc), hours=1)
    assert digest.total == 1
    assert digest.entries[0].title == "Vioris Docs"


def test_hours_window_includes_more(hc: HistoryConnector) -> None:
    hc.connect_snapshot(_source(hc))
    digest = hc.recent_history(_source(hc), hours=24 * 30)
    assert digest.total == 3


def test_entries_carry_summary_and_timestamp(hc: HistoryConnector) -> None:
    hc.connect_snapshot(_source(hc))
    digest = hc.recent_history(_source(hc), hours=1)
    e = digest.entries[0]
    assert e.url == "https://vioris.example/docs"
    assert e.title == "Vioris Docs"
    assert e.visited_at is not None
    assert e.summary == "Vioris Docs — https://vioris.example/docs"


def test_disconnect_forgets_source(hc: HistoryConnector) -> None:
    hc.connect_snapshot(_source(hc))
    hc.disconnect_snapshot(_source(hc))
    assert hc._vault.load("history", _source(hc)) is None
    with pytest.raises(ConnectionMissingError):
        hc.recent_history(_source(hc))


def test_missing_file_is_loud(hc: HistoryConnector, mock: MockHistoryTransport) -> None:
    hc.connect_snapshot(_source(hc))
    mock.fail_missing = True
    with pytest.raises(ConnectorError, match="not found"):
        hc.recent_history(_source(hc))


def test_corrupt_file_is_loud(hc: HistoryConnector, mock: MockHistoryTransport) -> None:
    hc.connect_snapshot(_source(hc))
    mock.fail_corrupt = True
    with pytest.raises(ConnectorError, match="unreadable"):
        hc.recent_history(_source(hc))


def test_local_transport_reads_chromium_db_readonly(tmp_path) -> None:
    f = tmp_path / "History"
    conn = sqlite3.connect(str(f))
    conn.execute("CREATE TABLE urls (url TEXT, title TEXT, last_visit_time INTEGER)")
    conn.execute(
        "INSERT INTO urls VALUES (?, ?, ?)",
        ("https://alpha.example", "Alpha", _chromium_now_ts()),
    )
    conn.commit()
    conn.close()

    transport = LocalHistoryTransport()
    records = transport.read_recent(str(f), hours=72)
    assert len(records) == 1
    assert records[0]["title"] == "Alpha"

    # The connector must never write to the profile: open it read-only again.
    ro = sqlite3.connect(f"file:{f.as_posix()}?mode=ro", uri=True)
    try:
        assert ro.execute("SELECT count(*) FROM urls").fetchone()[0] == 1
    finally:
        ro.close()


def test_local_transport_missing_file(tmp_path) -> None:
    with pytest.raises(ConnectorError, match="not found"):
        LocalHistoryTransport().read_recent(str(tmp_path / "nope"), hours=72)


def test_local_transport_non_db_file_is_loud(tmp_path) -> None:
    f = tmp_path / "History"
    f.write_text("not a database", encoding="utf-8")
    with pytest.raises(ConnectorError, match="unreadable"):
        LocalHistoryTransport().read_recent(str(f), hours=72)


def test_dt_from_chromium() -> None:
    dt = _dt_from_chromium(0)
    assert dt is not None and dt.year == 1601
    assert _dt_from_chromium(None) is None


def test_summary_falls_back_to_url() -> None:
    assert _summary("", "https://x.example") == "https://x.example"


def _chromium_now_ts() -> int:
    from datetime import datetime, timezone

    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - epoch).total_seconds() * 1_000_000)
