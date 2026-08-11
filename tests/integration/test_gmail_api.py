"""
Integration tests for the Phase 6 Gmail connector service per Prompt 6.1.

Boots the FastAPI app against the mock transport so the full HTTP surface
(connect → digest → status → disconnect) runs with zero network, and asserts
the endpoint falls back to the permission engine's static classification.
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

import integrations.gmail.api as gmail_api
from integrations.mock_providers.gmail_mock import MockGmailTransport


def _make_client(transport: MockGmailTransport) -> TestClient:
    # isolate the token vault + connector in a temp dir; swap transport for mock
    gmail_api._vault = None
    gmail_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-gmail-test-")

    # Other test modules (e.g. test_task_stop_all) freeze the registry and
    # never reset it. Make this file deterministic: fresh, unfrozen registry
    # with the phase-6 tools registered so the lifespan can't double-register.
    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.gmail.connector import GmailConnector

        return GmailConnector(gmail_api.get_vault(), transport=transport)

    gmail_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(gmail_api.app)
    client.__enter__()
    return client


def test_digest_requires_no_account_first() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/digest")
        assert resp.status_code == 409  # no account connected yet
        assert "connect/start" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_full_connect_digest_disconnect_lifecycle() -> None:
    transport = MockGmailTransport()
    transport._auth_scope = "https://www.googleapis.com/auth/gmail.readonly"
    client = _make_client(transport)
    try:
        # start OAuth
        start = client.post("/connect/start", json={"redirect_uri": "http://127.0.0.1:8445/authorized"})
        assert start.status_code == 200
        state = start.json()["state"]

        # complete OAuth with the mock code
        done = client.post(
            f"/connect/complete?state={state}&code=mockcode",
        )
        assert done.status_code == 200
        assert done.json()["account"] == "mock-user@gmail.com"

        # digest now flows
        digest = client.get("/digest")
        assert digest.status_code == 200
        body = digest.json()
        assert body["account"] == "mock-user@gmail.com"
        assert body["total_unread"] == 3
        assert body["urgencies"]["high"] >= 1
        assert any("URGENT" in i["subject"] for i in body["items"])

        # status reflects the connected account
        accounts = client.get("/accounts").json()
        assert accounts[0]["account"] == "mock-user@gmail.com"
        assert "read" in accounts[0]["permission_explanation"].lower()

        # disconnect revokes + deletes
        rev = client.post("/disconnect/mock-user@gmail.com")
        assert rev.status_code == 200
        assert transport.revoked is True
        assert client.get("/accounts").json() == []
    finally:
        client.__exit__(None, None, None)


def test_digest_blocked_when_tool_unregistered() -> None:
    """The gate is the engine, not the endpoint: unregistering the tool blocks
    the read before it happens."""
    transport = MockGmailTransport()
    transport._auth_scope = "https://www.googleapis.com/auth/gmail.readonly"
    client = _make_client(transport)
    try:
        client.post("/connect/start", json={})
        # capture a valid state→code pair against the mock transport
        state = client.post("/connect/start", json={}).json()["state"]
        client.post(f"/connect/complete?state={state}&code=c")
        assert client.get("/digest").status_code == 200

        # brute-force: save original registration, and simulate a frozen pre-Phase-6
        # engine that never registered the gmail tool — must 403.
        gmail_api.get_connector().gmail_read_registered_before = PermissionEngine.is_registered(
            "gmail.read_unread"
        )
        PermissionEngine._registry.pop("gmail.read_unread", None)
        resp = client.get("/digest")
        assert resp.status_code == 403
        # restore for other tests
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="gmail.read_unread",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["gmail_read_unread_observed"] is True
    finally:
        client.__exit__(None, None, None)