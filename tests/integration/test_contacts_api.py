"""
Integration tests for the Phase 6 Contacts connector service.

Boots the FastAPI app against the mock transport: connect → search → accounts →
disconnect, plus the permission-engine gate refusing reads when the tool is
unregistered.
"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

import integrations.contacts.api as contacts_api
from integrations.mock_providers.contacts_mock import MockContactsTransport
from packages.shared.permission_engine import (
    PermissionEngine,
    RiskTier,
    register_phase6_connector_tools,
)


def _make_client(transport: MockContactsTransport) -> TestClient:
    contacts_api._vault = None
    contacts_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-contacts-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.contacts.connector import ContactsConnector

        return ContactsConnector(contacts_api.get_vault(), transport=transport)

    contacts_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(contacts_api.app)
    client.__enter__()
    return client


def test_search_requires_no_account_first() -> None:
    transport = MockContactsTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/search")
        assert resp.status_code == 409
        assert "connect/start" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_full_connect_search_disconnect_lifecycle() -> None:
    transport = MockContactsTransport()
    transport._auth_scope = "https://www.googleapis.com/auth/contacts.readonly"
    client = _make_client(transport)
    try:
        start = client.post("/connect/start", json={"redirect_uri": "http://127.0.0.1:8450/authorized"})
        assert start.status_code == 200
        state = start.json()["state"]

        done = client.post("/connect/complete", params={"state": state, "code": "contactscode"})
        assert done.status_code == 200
        assert done.json()["account"] == "mock-user@gmail.com"

        search = client.get("/search", params={"query": "bob"})
        assert search.status_code == 200
        body = search.json()
        assert body["total"] == 1
        assert body["contacts"][0]["name"] == "Bob Miller"

        accounts = client.get("/accounts").json()
        assert accounts[0]["account"] == "mock-user@gmail.com"
        assert "read" in accounts[0]["permission_explanation"].lower()

        rev = client.post("/disconnect/mock-user@gmail.com")
        assert rev.status_code == 200
        assert transport.revoked is True
        assert client.get("/accounts").json() == []
    finally:
        client.__exit__(None, None, None)


def test_search_blocked_when_tool_unregistered() -> None:
    transport = MockContactsTransport()
    client = _make_client(transport)
    try:
        client.post("/connect/start", json={})
        state = client.post("/connect/start", json={}).json()["state"]
        client.post("/connect/complete", params={"state": state, "code": "c"})
        assert client.get("/search").status_code == 200

        PermissionEngine._registry.pop("contacts.search", None)
        resp = client.get("/search")
        assert resp.status_code == 403
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="contacts.search",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockContactsTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["contacts_search_observed"] is True
    finally:
        client.__exit__(None, None, None)