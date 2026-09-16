"""
Integration tests for the Phase 6 Reservations (booking) connector service.

Boots the FastAPI app against the mock booking transport: link → search →
create (gated by an approved ledger record) → unlink, plus the permission gates
refusing search at non-observe and create at non-execute.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

from fastapi.testclient import TestClient

import integrations.reservations.api as res_api
from integrations.mock_providers.reservations_mock import MockReservationsTransport
from packages.shared.permission_engine import (
    PermissionEngine,
    RiskTier,
    register_phase6_connector_tools,
)
from packages.shared.schemas import ToolRegistration


def _make_client(transport: MockReservationsTransport) -> TestClient:
    res_api._vault = None
    res_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-res-test-")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.reservations.connector import ReservationsConnector

        return ReservationsConnector(res_api.get_vault(), transport=transport)

    res_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(res_api.app)
    client.__enter__()
    return client


def _link(client: TestClient, transport: MockReservationsTransport, ref: str = "sess-res") -> None:
    client.post("/link/start")
    transport.complete_scan(ref)
    linked = client.post("/link/complete", params={"session_ref": ref})
    assert linked.status_code == 200


def _approve(task_id: str, step_id: str, idempotency_key: str) -> None:
    from services.task_runner.app.approvals import ApprovalStore

    store = ApprovalStore(pathlib.Path(os.environ["VIORUS_DATA_DIR"]) / "task_runner.db")
    approval = store.create(task_id, step_id, idempotency_key, "reservations.create", {"venue": "Trattoria Roma"})
    store.approve(approval)


def test_search_requires_session_first() -> None:
    transport = MockReservationsTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/search", params={"venue": "Trattoria Roma"})
        assert resp.status_code == 409
    finally:
        client.__exit__(None, None, None)


def test_search_returns_only_available_slots() -> None:
    transport = MockReservationsTransport()
    client = _make_client(transport)
    try:
        _link(client, transport)
        resp = client.get("/search", params={"venue": "Trattoria Roma", "party_size": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2  # unavailable slot dropped
        assert all(s["slot_id"] != "slot-2" for s in body["slots"])
    finally:
        client.__exit__(None, None, None)


def test_search_blocked_when_tool_unregistered() -> None:
    transport = MockReservationsTransport()
    client = _make_client(transport)
    try:
        _link(client, transport)
        assert client.get("/search", params={"venue": "V"}).status_code == 200

        PermissionEngine._registry.pop("reservations.search_slots", None)
        resp = client.get("/search", params={"venue": "V"})
        assert resp.status_code == 403
        PermissionEngine.register(
            ToolRegistration(
                tool_name="reservations.search_slots",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_create_requires_idempotency_key() -> None:
    transport = MockReservationsTransport()
    client = _make_client(transport)
    try:
        resp = client.post("/create", json={"slot_id": "slot-1", "guest_name": "Me"})
        assert resp.status_code == 422
        assert transport.bookings == []
    finally:
        client.__exit__(None, None, None)


def test_create_refused_without_approved_ledger() -> None:
    transport = MockReservationsTransport()
    client = _make_client(transport)
    try:
        _link(client, transport)
        resp = client.post(
            "/create",
            json={
                "slot_id": "slot-1",
                "venue": "Trattoria Roma",
                "at": "2026-09-01T19:00:00",
                "party_size": 2,
                "guest_name": "Me",
                "idempotency_key": "res-y",
                "task_id": "task-y",
                "step_id": "step-y",
            },
        )
        assert resp.status_code == 403
        assert "no approved approval" in resp.json()["detail"]
        assert transport.bookings == []  # the refusal is the point
    finally:
        client.__exit__(None, None, None)


def test_create_fires_with_approved_ledger_and_is_idempotent() -> None:
    transport = MockReservationsTransport()
    client = _make_client(transport)
    try:
        _link(client, transport)
        _approve("task-live", "step-live", "res-live")
        payload = {
            "slot_id": "slot-1",
            "venue": "Trattoria Roma",
            "at": "2026-09-01T19:00:00",
            "party_size": 2,
            "guest_name": "Me",
            "idempotency_key": "res-live",
            "task_id": "task-live",
            "step_id": "step-live",
        }
        first = client.post("/create", json=payload)
        assert first.status_code == 200
        body = first.json()
        assert body["replay"] is False
        assert body["reservation_id"] == "res_1"
        assert len(transport.bookings) == 1

        second = client.post("/create", json=payload)
        assert second.status_code == 200
        assert second.json()["replay"] is True
        assert second.json()["reservation_id"] == body["reservation_id"]
        assert len(transport.bookings) == 1  # duplicate retry never books twice
    finally:
        client.__exit__(None, None, None)


def test_create_blocked_when_tool_unregistered() -> None:
    transport = MockReservationsTransport()
    client = _make_client(transport)
    try:
        _link(client, transport)
        PermissionEngine._registry.pop("reservations.create", None)
        resp = client.post(
            "/create",
            json={
                "slot_id": "slot-1",
                "venue": "V",
                "at": "2026-09-01T19:00:00",
                "party_size": 2,
                "guest_name": "Me",
                "idempotency_key": "res-z",
                "task_id": "task-z",
                "step_id": "step-z",
            },
        )
        assert resp.status_code == 403
        assert transport.bookings == []
        PermissionEngine.register(
            ToolRegistration(
                tool_name="reservations.create",
                tier=RiskTier.EXECUTE,
                confirmation_required=True,
                description="restored",
                diff_card_fields=["venue", "at", "party_size", "guest_name", "slot_id", "channel"],
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    transport = MockReservationsTransport()
    client = _make_client(transport)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["reservations_search_slots_observed"] is True
        assert resp.json()["reservations_create_execute"] is True
    finally:
        client.__exit__(None, None, None)
