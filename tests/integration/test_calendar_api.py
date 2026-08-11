"""
Integration tests for the Phase 6 Calendar connector service.

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

import integrations.calendar.api as cal_api
from integrations.mock_providers.calendar_mock import MockCalendarTransport


def _make_client(transport: MockCalendarTransport) -> TestClient:
    cal_api._vault = None
    cal_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-cal-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.calendar.connector import CalendarConnector

        return CalendarConnector(cal_api.get_vault(), transport=transport)

    cal_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(cal_api.app)
    client.__enter__()
    return client


def test_digest_requires_no_account_first() -> None:
    transport = MockCalendarTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/digest")
        assert resp.status_code == 409
        assert "connect/start" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_full_connect_digest_disconnect_lifecycle() -> None:
    transport = MockCalendarTransport()
    transport._auth_scope = "https://www.googleapis.com/auth/calendar.readonly"
    client = _make_client(transport)
    try:
        start = client.post("/connect/start", json={"redirect_uri": "http://127.0.0.1:8447/authorized"})
        assert start.status_code == 200
        state = start.json()["state"]

        done = client.post("/connect/complete", params={"state": state, "code": "calcode"})
        assert done.status_code == 200
        assert done.json()["account"] == "mock-user@gmail.com"

        digest = client.get("/digest")
        assert digest.status_code == 200
        body = digest.json()
        assert body["account"] == "mock-user@gmail.com"
        assert body["total"] == 3
        titles = {e["title"] for e in body["events"]}
        assert "URGENT: project deadline review" in titles
        urgent = next(e for e in body["events"] if e["title"] == "URGENT: project deadline review")
        assert urgent["urgency"] == "high"

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
    transport = MockCalendarTransport()
    client = _make_client(transport)
    try:
        client.post("/connect/start", json={})
        state = client.post("/connect/start", json={}).json()["state"]
        client.post("/connect/complete", params={"state": state, "code": "c"})
        assert client.get("/digest").status_code == 200

        PermissionEngine._registry.pop("calendar.read_upcoming", None)
        resp = client.get("/digest")
        assert resp.status_code == 403
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="calendar.read_upcoming",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockCalendarTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["calendar_read_upcoming_observed"] is True
    finally:
        client.__exit__(None, None, None)