"""
Integration tests for the Phase 6 Bookmarks connector service.

Boots the FastAPI app against the mock transport: connect → search → sources →
disconnect, plus the permission-engine gate refusing reads when the tool is
unregistered.
"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from packages.shared.permission_engine import (
    PermissionEngine,
    RiskTier,
    register_phase6_connector_tools,
)

import integrations.bookmarks.api as bm_api
from integrations.mock_providers.bookmarks_mock import MockBookmarksTransport


def _make_client(transport: MockBookmarksTransport, tmp_path=None) -> TestClient:
    bm_api._vault = None
    bm_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-bm-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    # connect_snapshot validates the source exists on disk (real safety).
    import pathlib
    source_dir = pathlib.Path(tmp_path) if tmp_path else pathlib.Path(tempfile.mkdtemp())
    source_file = source_dir / "bookmarks.json"
    source_file.write_text("{}", encoding="utf-8")
    _SOURCE = str(source_file)

    def _patched_get_connector():
        from integrations.bookmarks.connector import BookmarksConnector

        return BookmarksConnector(bm_api.get_vault(), transport=transport)

    bm_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(bm_api.app)
    client.__enter__()
    client._source = _SOURCE
    return client


def test_search_requires_no_source_first(tmp_path) -> None:
    transport = MockBookmarksTransport()
    client = _make_client(transport, tmp_path)
    try:
        resp = client.get("/search")
        assert resp.status_code == 409
        assert "/connect" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_full_connect_search_disconnect_lifecycle(tmp_path) -> None:
    transport = MockBookmarksTransport()
    client = _make_client(transport, tmp_path)
    source = client._source
    try:
        conn = client.post("/connect", json={"source": source})
        assert conn.status_code == 200
        assert conn.json()["source"] == source

        search = client.get("/search", params={"query": "vioris"})
        assert search.status_code == 200
        body = search.json()
        assert body["total"] == 1
        assert body["bookmarks"][0]["title"] == "Vioris Docs"
        assert body["bookmarks"][0]["url"] == "https://example.com/vioris"

        sources = client.get("/sources").json()
        assert sources[0]["account"] == source
        assert "read" in sources[0]["permission_explanation"].lower()

        disc = client.post(f"/disconnect/{source}")
        assert disc.status_code == 200
        assert client.get("/sources").json() == []
    finally:
        client.__exit__(None, None, None)


def test_search_blocked_when_tool_unregistered(tmp_path) -> None:
    transport = MockBookmarksTransport()
    client = _make_client(transport, tmp_path)
    source = client._source
    try:
        client.post("/connect", json={"source": source})
        assert client.get("/search").status_code == 200

        PermissionEngine._registry.pop("bookmarks.search", None)
        resp = client.get("/search")
        assert resp.status_code == 403
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="bookmarks.search",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockBookmarksTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["bookmarks_search_observed"] is True
    finally:
        client.__exit__(None, None, None)