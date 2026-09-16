"""
Integration tests for the Phase 6 Telephony (calling) connector service.

Boots the FastAPI app against the mock transport: prepare (shown, never dialed)
and start (native-dialer handoff gated by an approved ledger record). Verifies
the permission-engine gates refuse prepare at non-prepare and start at
non-execute.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

from fastapi.testclient import TestClient

import integrations.telephony.api as tel_api
from integrations.mock_providers.telephony_mock import MockTelephonyTransport
from packages.shared.permission_engine import (
    PermissionEngine,
    RiskTier,
    register_phase6_connector_tools,
)
from packages.shared.schemas import ToolRegistration


def _make_client(transport: MockTelephonyTransport) -> TestClient:
    tel_api._vault = None
    tel_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-tel-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.telephony.connector import TelephonyConnector

        return TelephonyConnector(tel_api.get_vault(), transport=transport)

    tel_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(tel_api.app)
    client.__enter__()
    return client


def _approve_call(task_id: str, step_id: str, idempotency_key: str) -> None:
    from services.task_runner.app.approvals import ApprovalStore

    store = ApprovalStore(pathlib.Path(os.environ["VIORUS_DATA_DIR"]) / "task_runner.db")
    approval = store.create(task_id, step_id, idempotency_key, "telephony.start_call", {"recipient": "Mum"})
    store.approve(approval)


def test_prepare_drafts_brief() -> None:
    transport = MockTelephonyTransport()
    client = _make_client(transport)
    try:
        resp = client.post("/prepare", json={"recipient": "Mum", "purpose": "confirm dinner"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["drafted"] is True
        assert body["recipient_identity"] == "Mum <+447700900001>"
        assert "confirm dinner" in body["script"]
        assert transport.handoffs == []  # prepared, never dialed
    finally:
        client.__exit__(None, None, None)


def test_prepare_blocked_when_tool_unregistered() -> None:
    transport = MockTelephonyTransport()
    client = _make_client(transport)
    try:
        PermissionEngine._registry.pop("telephony.prepare_call", None)
        resp = client.post("/prepare", json={"recipient": "Mum"})
        assert resp.status_code == 403
        PermissionEngine.register(
            ToolRegistration(
                tool_name="telephony.prepare_call",
                tier=RiskTier.PREPARE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_start_requires_idempotency_key() -> None:
    transport = MockTelephonyTransport()
    client = _make_client(transport)
    try:
        resp = client.post("/start", json={"recipient": "Mum"})
        assert resp.status_code == 422
        assert transport.handoffs == []
    finally:
        client.__exit__(None, None, None)


def test_start_refused_without_approved_ledger() -> None:
    transport = MockTelephonyTransport()
    client = _make_client(transport)
    try:
        resp = client.post(
            "/start",
            json={
                "recipient": "Mum",
                "idempotency_key": "call-y",
                "task_id": "task-y",
                "step_id": "step-y",
            },
        )
        assert resp.status_code == 403
        assert "no approved approval" in resp.json()["detail"]
        assert transport.handoffs == []  # the refusal is the point
    finally:
        client.__exit__(None, None, None)


def test_start_fires_with_approved_ledger_and_is_idempotent() -> None:
    transport = MockTelephonyTransport()
    client = _make_client(transport)
    try:
        _approve_call("task-live", "step-live", "call-live")
        payload = {
            "recipient": "Mum",
            "script": "Hi Mum, checking in.",
            "idempotency_key": "call-live",
            "task_id": "task-live",
            "step_id": "step-live",
        }
        first = client.post("/start", json=payload)
        assert first.status_code == 200
        body = first.json()
        assert body["replay"] is False
        assert body["recipient_identity"] == "Mum <+447700900001>"
        assert body["channel"] == "native-dialer"
        assert len(transport.handoffs) == 1

        second = client.post("/start", json=payload)
        assert second.status_code == 200
        assert second.json()["replay"] is True
        assert second.json()["handoff_id"] == body["handoff_id"]
        assert len(transport.handoffs) == 1  # never opened the dialer twice
    finally:
        client.__exit__(None, None, None)


def test_start_ambiguous_recipient_returns_400() -> None:
    transport = MockTelephonyTransport()
    client = _make_client(transport)
    try:
        _approve_call("task-amb", "step-amb", "call-amb")
        resp = client.post(
            "/start",
            json={
                "recipient": "Alex",
                "idempotency_key": "call-amb",
                "task_id": "task-amb",
                "step_id": "step-amb",
            },
        )
        assert resp.status_code == 400
        assert "ambiguous" in resp.json()["detail"]
        assert transport.handoffs == []
    finally:
        client.__exit__(None, None, None)


def test_start_blocked_when_tool_unregistered() -> None:
    transport = MockTelephonyTransport()
    client = _make_client(transport)
    try:
        PermissionEngine._registry.pop("telephony.start_call", None)
        resp = client.post(
            "/start",
            json={
                "recipient": "Mum",
                "idempotency_key": "call-z",
                "task_id": "task-z",
                "step_id": "step-z",
            },
        )
        assert resp.status_code == 403
        assert transport.handoffs == []
        PermissionEngine.register(
            ToolRegistration(
                tool_name="telephony.start_call",
                tier=RiskTier.EXECUTE,
                confirmation_required=True,
                description="restored",
                diff_card_fields=["recipient", "recipient_identity", "phone", "script", "channel"],
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockTelephonyTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["telephony_prepare_call_prepare"] is True
        assert resp.json()["telephony_start_call_execute"] is True
    finally:
        client.__exit__(None, None, None)
