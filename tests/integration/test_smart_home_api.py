"""
Integration tests for the Phase 6 Smart-home connector service.

Boots the FastAPI app against the mock local hub: pair → status (Observe) →
control (Execute, gated by an approved ledger record) → unlink, plus the
permission gates and the safety-critical refusal.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

from fastapi.testclient import TestClient

import integrations.smart_home.api as sh_api
from integrations.mock_providers.smart_home_mock import MockSmartHomeTransport
from packages.shared.permission_engine import (
    PermissionEngine,
    RiskTier,
    register_phase6_connector_tools,
)
from packages.shared.schemas import ToolRegistration


def _make_client(transport: MockSmartHomeTransport) -> TestClient:
    sh_api._vault = None
    sh_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-sh-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.smart_home.connector import SmartHomeConnector

        return SmartHomeConnector(sh_api.get_vault(), transport=transport)

    sh_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(sh_api.app)
    client.__enter__()
    return client


def _pair(client: TestClient, transport: MockSmartHomeTransport, ref: str = "hub-sh") -> None:
    client.post("/link/start")
    transport.complete_pair(ref)
    linked = client.post("/link/complete", params={"hub_ref": ref})
    assert linked.status_code == 200


def _approve(task_id: str, step_id: str, idempotency_key: str) -> None:
    from services.task_runner.app.approvals import ApprovalStore

    store = ApprovalStore(pathlib.Path(os.environ["VIORUS_DATA_DIR"]) / "task_runner.db")
    approval = store.create(task_id, step_id, idempotency_key, "smart_home.control", {"device_id": "light-living"})
    store.approve(approval)


def test_status_requires_hub_first() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/status")
        assert resp.status_code == 409
    finally:
        client.__exit__(None, None, None)


def test_status_lists_devices() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        _pair(client, transport)
        resp = client.get("/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert any(d["device_id"] == "lock-front" for d in body["devices"])
    finally:
        client.__exit__(None, None, None)


def test_status_blocked_when_tool_unregistered() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        _pair(client, transport)
        assert client.get("/status").status_code == 200

        PermissionEngine._registry.pop("smart_home.device_status", None)
        resp = client.get("/status")
        assert resp.status_code == 403
        PermissionEngine.register(
            ToolRegistration(
                tool_name="smart_home.device_status",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_control_requires_idempotency_key() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        resp = client.post("/control", json={"device_id": "light-living", "action": "on", "value": "on"})
        assert resp.status_code == 422
        assert transport.controls == []
    finally:
        client.__exit__(None, None, None)


def test_control_refused_without_approved_ledger() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        _pair(client, transport)
        resp = client.post(
            "/control",
            json={
                "device_id": "light-living",
                "action": "on",
                "value": "on",
                "idempotency_key": "ctl-y",
                "task_id": "task-y",
                "step_id": "step-y",
            },
        )
        assert resp.status_code == 403
        assert "no approved approval" in resp.json()["detail"]
        assert transport.controls == []  # the refusal is the point
    finally:
        client.__exit__(None, None, None)


def test_control_fires_with_approved_ledger_and_is_idempotent() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        _pair(client, transport)
        _approve("task-live", "step-live", "ctl-live")
        payload = {
            "device_id": "light-living",
            "action": "on",
            "value": "on",
            "idempotency_key": "ctl-live",
            "task_id": "task-live",
            "step_id": "step-live",
        }
        first = client.post("/control", json=payload)
        assert first.status_code == 200
        body = first.json()
        assert body["replay"] is False
        assert body["result"] == "applied"
        assert len(transport.controls) == 1

        second = client.post("/control", json=payload)
        assert second.status_code == 200
        assert second.json()["replay"] is True
        assert len(transport.controls) == 1  # never re-applied
    finally:
        client.__exit__(None, None, None)


def test_control_safety_critical_refused() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        _pair(client, transport)
        _approve("task-lock", "step-lock", "ctl-lock")
        resp = client.post(
            "/control",
            json={
                "device_id": "lock-front",
                "action": "unlock",
                "value": "unlock",
                "idempotency_key": "ctl-lock",
                "task_id": "task-lock",
                "step_id": "step-lock",
            },
        )
        assert resp.status_code == 400
        assert "safety-critical" in resp.json()["detail"]
        assert transport.controls == []  # an approval cannot override this
    finally:
        client.__exit__(None, None, None)


def test_control_blocked_when_tool_unregistered() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        _pair(client, transport)
        PermissionEngine._registry.pop("smart_home.control", None)
        resp = client.post(
            "/control",
            json={
                "device_id": "light-living",
                "action": "on",
                "value": "on",
                "idempotency_key": "ctl-z",
                "task_id": "task-z",
                "step_id": "step-z",
            },
        )
        assert resp.status_code == 403
        assert transport.controls == []
        PermissionEngine.register(
            ToolRegistration(
                tool_name="smart_home.control",
                tier=RiskTier.EXECUTE,
                confirmation_required=True,
                description="restored",
                diff_card_fields=["device_id", "device_name", "category", "action", "value", "channel"],
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockSmartHomeTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["smart_home_device_status_observed"] is True
        assert resp.json()["smart_home_control_execute"] is True
    finally:
        client.__exit__(None, None, None)
