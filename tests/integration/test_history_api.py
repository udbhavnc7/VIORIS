"""
Integration tests for the Phase 6 History connector service.

Boots the FastAPI app against the mock transport: connect → recent → sources →
disconnect, plus the permission-engine gate refusing reads when the tool is
unregistered.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

from fastapi.testclient import TestClient

import integrations.history.api as hist_api
from integrations.mock_providers.history_mock import MockHistoryTransport
from packages.shared.permission_engine import (
    PermissionEngine,
    RiskTier,
    register_phase6_connector_tools,
)


def _make_client(transport: MockHistoryTransport, tmp_path=None) -> TestClient:
    hist_api._vault = None
    hist_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-hist-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    # connect_snapshot validates the source exists on disk (real safety).
    source_dir = pathlib.Path(tmp_path) if tmp_path else pathlib.Path(tempfile.mkdtemp())
    source_file = source_dir / "History"
    source_file.write_text("", encoding="utf-8")
    _SOURCE = str(source_file)

    def _patched_get_connector():
        from integrations.history.connector import HistoryConnector

        return HistoryConnector(hist_api.get_vault(), transport=transport)

    hist_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(hist_api.app)
    client.__enter__()
    client._source = _SOURCE
    return client


def test_recent_requires_no_source_first(tmp_path) -> None:
    transport = MockHistoryTransport()
    client = _make_client(transport, tmp_path)
    try:
        resp = client.get("/recent")
        assert resp.status_code == 409
        assert "/connect" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_full_connect_recent_disconnect_lifecycle(tmp_path) -> None:
    transport = MockHistoryTransport()
    client = _make_client(transport, tmp_path)
    source = client._source
    try:
        conn = client.post("/connect", json={"source": source})
        assert conn.status_code == 200
        assert conn.json()["source"] == source

        recent = client.get("/recent")
        assert recent.status_code == 200
        body = recent.json()
        assert body["total"] == 2  # 72h default window
        assert body["entries"][0]["title"] == "Vioris Docs"

        narrow = client.get("/recent", params={"hours": 1}).json()
        assert narrow["total"] == 1

        sources = client.get("/sources").json()
        assert sources[0]["account"] == source
        assert "read" in sources[0]["permission_explanation"].lower()

        disc = client.post(f"/disconnect/{source}")
        assert disc.status_code == 200
        assert client.get("/sources").json() == []
    finally:
        client.__exit__(None, None, None)


def test_recent_blocked_when_tool_unregistered(tmp_path) -> None:
    transport = MockHistoryTransport()
    client = _make_client(transport, tmp_path)
    source = client._source
    try:
        client.post("/connect", json={"source": source})
        assert client.get("/recent").status_code == 200

        PermissionEngine._registry.pop("history.recent", None)
        resp = client.get("/recent")
        assert resp.status_code == 403
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="history.recent",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration(tmp_path) -> None:
    transport = MockHistoryTransport()
    client = _make_client(transport, tmp_path)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["history_recent_observed"] is True
    finally:
        client.__exit__(None, None, None)
