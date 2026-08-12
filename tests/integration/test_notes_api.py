"""
Integration tests for the Phase 6 Notes connector service.

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

import integrations.notes.api as notes_api
from integrations.mock_providers.notes_mock import MockNotesTransport


def _make_client(transport: MockNotesTransport) -> TestClient:
    notes_api._vault = None
    notes_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-notes-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.notes.connector import NotesConnector

        return NotesConnector(notes_api.get_vault(), transport=transport)

    notes_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(notes_api.app)
    client.__enter__()
    return client


def test_digest_requires_no_account_first() -> None:
    transport = MockNotesTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/digest")
        assert resp.status_code == 409
        assert "connect/start" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_full_connect_digest_disconnect_lifecycle() -> None:
    transport = MockNotesTransport()
    transport._auth_scope = "https://www.googleapis.com/auth/documents.readonly"
    client = _make_client(transport)
    try:
        start = client.post("/connect/start", json={"redirect_uri": "http://127.0.0.1:8449/authorized"})
        assert start.status_code == 200
        state = start.json()["state"]

        done = client.post("/connect/complete", params={"state": state, "code": "notescode"})
        assert done.status_code == 200
        assert done.json()["account"] == "mock-user@gmail.com"

        digest = client.get("/digest")
        assert digest.status_code == 200
        body = digest.json()
        assert body["total"] == 3
        titles = {n["title"] for n in body["notes"]}
        assert "Grocery list" in titles
        grocery = next(n for n in body["notes"] if n["title"] == "Grocery list")
        assert "Eggs" in grocery["body"]

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
    transport = MockNotesTransport()
    client = _make_client(transport)
    try:
        client.post("/connect/start", json={})
        state = client.post("/connect/start", json={}).json()["state"]
        client.post("/connect/complete", params={"state": state, "code": "c"})
        assert client.get("/digest").status_code == 200

        PermissionEngine._registry.pop("notes.list_recent", None)
        resp = client.get("/digest")
        assert resp.status_code == 403
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="notes.list_recent",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockNotesTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["notes_list_recent_observed"] is True
    finally:
        client.__exit__(None, None, None)