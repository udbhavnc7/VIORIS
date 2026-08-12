"""
Unit tests for the Phase 6 Bookmarks connector.

Covers: local-snapshot lifecycle (connect / disconnect), encrypted vault,
read-only capability surface, local query filtering, and loud unreadable-file
handling. Also exercises the real LocalBookmarksTransport against a temp
Chromium-shaped file (no network).
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from integrations.base import (
    ConnectionMissingError,
    ConnectorError,
)
from integrations.bookmarks.connector import (
    BookmarksConnector,
    BookmarksDigest,
    _walk_bookmark_tree,
)
from integrations.mock_providers.bookmarks_mock import MockBookmarksTransport
from integrations.token_vault import TokenVault


@pytest.fixture()
def vault(tmp_path) -> TokenVault:
    return TokenVault(tmp_path / "bm.db", key=Fernet.generate_key())


@pytest.fixture()
def mock() -> MockBookmarksTransport:
    return MockBookmarksTransport()


@pytest.fixture()
def bm(vault: TokenVault, mock: MockBookmarksTransport, tmp_path) -> BookmarksConnector:
    # connect_snapshot validates the source exists on disk (real safety), so
    # give the mock-based tests a real (dummy) file to point at.
    src = tmp_path / "bookmarks.json"
    src.write_text("{}", encoding="utf-8")
    _bm = BookmarksConnector(vault, transport=mock)
    _bm._source = str(src)
    return _bm


def _source(bm: BookmarksConnector) -> str:
    return bm._source


def test_no_oauth_scopes_declared(bm: BookmarksConnector) -> None:
    scopes = bm.declared_scopes()
    assert scopes.write == []
    assert "browser-profile:read-bookmarks" in scopes.read
    assert "bookmarks.search" in bm.tools


def test_connect_validates_file_exists(vault: TokenVault, tmp_path) -> None:
    bm = BookmarksConnector(vault, transport=MockBookmarksTransport())
    with pytest.raises(ConnectorError, match="not found"):
        bm.connect_snapshot(str(tmp_path / "nope.json"))


def test_connect_and_search_lifecycle(bm: BookmarksConnector, mock: MockBookmarksTransport) -> None:
    bm.connect_snapshot(_source(bm))
    digest = bm.search_bookmarks(_source(bm))
    assert isinstance(digest, BookmarksDigest)
    assert digest.total == 3


def test_search_filters_by_query(bm: BookmarksConnector) -> None:
    bm.connect_snapshot(_source(bm))
    digest = bm.search_bookmarks(_source(bm), query="vioris")
    assert digest.total == 1
    assert digest.entries[0].title == "Vioris Docs"


def test_search_matches_folder(bm: BookmarksConnector) -> None:
    bm.connect_snapshot(_source(bm))
    digest = bm.search_bookmarks(_source(bm), query="Other bookmarks")
    assert digest.total == 1
    assert digest.entries[0].url == "https://example.com/recipes"


def test_disconnect_forgets_source(bm: BookmarksConnector) -> None:
    bm.connect_snapshot(_source(bm))
    bm.disconnect_snapshot(_source(bm))
    assert bm._vault.load("bookmarks", _source(bm)) is None
    with pytest.raises(ConnectionMissingError):
        bm.search_bookmarks(_source(bm))


def test_missing_file_is_loud(bm: BookmarksConnector, mock: MockBookmarksTransport) -> None:
    bm.connect_snapshot(_source(bm))
    mock.fail_missing = True
    with pytest.raises(ConnectorError, match="not found"):
        bm.search_bookmarks(_source(bm))


def test_corrupt_file_is_loud(bm: BookmarksConnector, mock: MockBookmarksTransport) -> None:
    bm.connect_snapshot(_source(bm))
    mock.fail_corrupt = True
    with pytest.raises(ConnectorError, match="unreadable"):
        bm.search_bookmarks(_source(bm))


def test_local_transport_reads_chromium_file(tmp_path) -> None:
    from integrations.bookmarks.connector import LocalBookmarksTransport

    f = tmp_path / "Bookmarks"
    f.write_text(json.dumps(_DEFAULT_CHROMIUM), encoding="utf-8")
    transport = LocalBookmarksTransport()
    records = transport.read_snapshot(str(f))
    assert len(records) == 2
    assert records[0]["title"] == "Alpha"
    assert records[0]["folder"] == "Bookmarks bar"


def test_walk_tree_nests_folders() -> None:
    node = {
        "name": "Bar",
        "type": "folder",
        "children": [
            {"name": "A", "url": "https://a", "type": "url"},
            {
                "name": "Sub",
                "type": "folder",
                "children": [{"name": "B", "url": "https://b", "type": "url"}],
            },
        ],
    }
    flat = _walk_bookmark_tree(node)
    titles = {(r["title"], r["folder"]) for r in flat}
    assert ("A", "Bar") in titles
    assert ("B", "Sub") in titles


_DEFAULT_CHROMIUM = {
    "roots": {
        "bookmark_bar": {
            "name": "Bookmarks bar",
            "type": "folder",
            "children": [
                {"name": "Alpha", "url": "https://alpha.example", "type": "url"},
                {"name": "Beta", "url": "https://beta.example", "type": "url"},
            ],
        }
    }
}