"""
Integration tests for the Phase 6 Cloud Files connector service.

Boots the FastAPI app against the mock transport: connect → digest → accounts →
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

import integrations.cloud_files.api as drive_api
from integrations.mock_providers.drive_mock import MockDriveTransport


def _make_client(transport: MockDriveTransport) -> TestClient:
    drive_api._vault = None
    drive_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-drive-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.cloud_files.connector import DriveConnector

        return DriveConnector(drive_api.get_vault(), transport=transport)

    drive_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(drive_api.app)
    client.__enter__()
    return client


def test_digest_requires_no_account_first() -> None:
    transport = MockDriveTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/digest")
        assert resp.status_code == 409
        assert "connect/start" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_full_connect_digest_disconnect_lifecycle() -> None:
    transport = MockDriveTransport()
    transport._auth_scope = "https://www.googleapis.com/auth/drive.metadata.readonly"
    client = _make_client(transport)
    try:
        start = client.post("/connect/start", json={"redirect_uri": "http://127.0.0.1:8448/authorized"})
        assert start.status_code == 200
        state = start.json()["state"]

        done = client.post("/connect/complete", params={"state": state, "code": "drivecode"})
        assert done.status_code == 200
        assert done.json()["account"] == "mock-user@gmail.com"

        digest = client.get("/digest")
        assert digest.status_code == 200
        body = digest.json()
        assert body["total"] == 3
        names = {f["name"] for f in body["files"]}
        assert "Q3 Budget.xlsx" in names
        assert "Launch Deck.pdf" in names

        accounts = client.get("/accounts").json()
        assert accounts[0]["account"] == "mock-user@gmail.com"
        assert "read" in accounts[0]["permission_explanation"].lower()

        rev = client.post("/disconnect/mock-user@gmail.com")
        assert rev.status_code == 200
        assert transport.revoked is True
        assert client.get("/accounts").json() == []
    finally:
        client.__exit__(None, None, None)


def test_digest_blocked_when_tool_unregistered() -> None:
    transport = MockDriveTransport()
    client = _make_client(transport)
    try:
        client.post("/connect/start", json={})
        state = client.post("/connect/start", json={}).json()["state"]
        client.post("/connect/complete", params={"state": state, "code": "c"})
        assert client.get("/digest").status_code == 200

        PermissionEngine._registry.pop("cloud_files.list_recent", None)
        resp = client.get("/digest")
        assert resp.status_code == 403
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="cloud_files.list_recent",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockDriveTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["cloud_files_list_recent_observed"] is True
    finally:
        client.__exit__(None, None, None)