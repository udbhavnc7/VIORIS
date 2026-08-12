"""
Integration tests for the Phase 6 WhatsApp connector service per Prompt 6.2.

Boots the FastAPI app against the mock browser-session transport: link →
digest → session list → unlink, plus the permission-engine gate refusing reads
when the tool is unregistered.
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

import integrations.whatsapp.api as wa_api
from integrations.mock_providers.whatsapp_mock import MockWhatsAppSession


def _make_client(session: MockWhatsAppSession) -> TestClient:
    wa_api._vault = None
    wa_api._connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-wa-test-")

    # Other test modules (e.g. test_task_stop_all) freeze the registry and
    # never reset it. Make this file deterministic: fresh, unfrozen registry
    # with the phase-6 tools registered so the lifespan can't double-register.
    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.whatsapp.connector import WhatsAppConnector

        return WhatsAppConnector(wa_api.get_vault(), transport=session)

    wa_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    client = TestClient(wa_api.app)
    client.__enter__()
    return client


def test_digest_requires_session_first() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        resp = client.get("/digest")
        assert resp.status_code == 409
        assert "link/start" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_full_link_digest_unlink_lifecycle() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        ls = client.post("/link/start", json={"session_name": "test"})
        assert ls.status_code == 200
        session.complete_scan("sess-live")  # the user scanned the QR side
        linked = client.post("/link/complete", params={"session_ref": "sess-live"})
        assert linked.status_code == 200

        digest = client.get("/digest")
        assert digest.status_code == 200
        body = digest.json()
        assert body["account"] == "sess-live"
        assert body["total"] >= 3
        assert body["flagged_hidden"] >= 1

        senders = {e["sender"] for e in body["entries"]}
        assert "Mum" in senders and "Dan (florist)" in senders
        florist = next(e for e in body["entries"] if e["sender"] == "Dan (florist)")
        assert florist["ask"] and florist["inaccessible_reason"] is None

        sessions = client.get("/sessions").json()
        assert sessions[0]["account"] == "sess-live"

        un = client.post("/unlink/sess-live")
        assert un.status_code == 200
        assert client.get("/sessions").json() == []
    finally:
        client.__exit__(None, None, None)


def test_digest_blocked_when_tool_unregistered() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        # get a valid session in place
        session.complete_scan("sess-x")
        linked = client.post("/link/complete", params={"session_ref": "sess-x"})
        assert linked.status_code == 200
        assert client.get("/digest").status_code == 200

        PermissionEngine._registry.pop("whatsapp.read_digest", None)
        resp = client.get("/digest")
        assert resp.status_code == 403
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="whatsapp.read_digest",
                tier=RiskTier.OBSERVE,
                confirmation_required=False,
                description="restored",
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_health_reports_registration() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["whatsapp_read_digest_observed"] is True
        assert resp.json()["whatsapp_send_message_execute"] is True
    finally:
        client.__exit__(None, None, None)


# ── send path (Execute-tier, approval-ledger-gated) ──────────────────────────


def _approve_send(task_id: str, step_id: str, idempotency_key: str) -> None:
    """Write an approved record into the task-runner ledger the verifier reads."""
    import os
    from pathlib import Path

    from services.task_runner.app.approvals import ApprovalStore

    store = ApprovalStore(Path(os.environ["VIORUS_DATA_DIR"]) / "task_runner.db")
    approval = store.create(
        task_id, step_id, idempotency_key, "whatsapp.send_message", {"recipient": "Mum"}
    )
    store.approve(approval)


def test_send_requires_idempotency_key() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        resp = client.post("/send", json={"recipient": "Mum", "content": "Hi"})
        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_send_blocked_when_tool_unregistered() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        session.complete_scan("sess-send-x")
        client.post("/link/complete", params={"session_ref": "sess-send-x"})

        PermissionEngine._registry.pop("whatsapp.send_message", None)
        resp = client.post(
            "/send",
            json={"recipient": "Mum", "content": "Hi", "idempotency_key": "k-x"},
        )
        assert resp.status_code == 403
        assert session.sent == []  # nothing fired
        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="whatsapp.send_message",
                tier=RiskTier.EXECUTE,
                confirmation_required=True,
                description="restored",
                diff_card_fields=["recipient", "recipient_identity", "content", "channel"],
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_send_refused_without_approved_ledger() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        session.complete_scan("sess-send-y")
        client.post("/link/complete", params={"session_ref": "sess-send-y"})
        # No approval record exists for this step+key.
        resp = client.post(
            "/send",
            json={
                "recipient": "Mum",
                "content": "Hi",
                "idempotency_key": "k-y",
                "task_id": "task-y",
                "step_id": "step-y",
            },
        )
        assert resp.status_code == 403
        assert "no approved approval" in resp.json()["detail"]
        assert session.sent == []  # the refusal is the point
    finally:
        client.__exit__(None, None, None)


def test_send_requires_session_first() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        resp = client.post(
            "/send",
            json={
                "recipient": "Mum",
                "content": "Hi",
                "idempotency_key": "k-z",
                "task_id": "task-z",
                "step_id": "step-z",
            },
        )
        assert resp.status_code == 409
    finally:
        client.__exit__(None, None, None)


def test_send_fires_with_approved_ledger_and_is_idempotent() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        session.complete_scan("sess-send-live")
        client.post("/link/complete", params={"session_ref": "sess-send-live"})
        _approve_send("task-live", "step-live", "k-live")

        payload = {
            "recipient": "Mum",
            "content": "On my way!",
            "idempotency_key": "k-live",
            "task_id": "task-live",
            "step_id": "step-live",
        }
        first = client.post("/send", json=payload)
        assert first.status_code == 200
        body = first.json()
        assert body["replay"] is False
        assert body["recipient_identity"] == "Mum <+447700900001>"  # resolved, not typed
        assert body["message_id"] == "sent_1"
        assert len(session.sent) == 1

        second = client.post("/send", json=payload)
        assert second.status_code == 200
        assert second.json()["replay"] is True
        assert second.json()["message_id"] == body["message_id"]
        assert len(session.sent) == 1  # duplicate retry never double-sends
    finally:
        client.__exit__(None, None, None)


def test_send_ambiguous_recipient_returns_400() -> None:
    session = MockWhatsAppSession()
    client = _make_client(session)
    try:
        session.complete_scan("sess-send-amb")
        client.post("/link/complete", params={"session_ref": "sess-send-amb"})
        _approve_send("task-amb", "step-amb", "k-amb")

        resp = client.post(
            "/send",
            json={
                "recipient": "Alex",
                "content": "Hi",
                "idempotency_key": "k-amb",
                "task_id": "task-amb",
                "step_id": "step-amb",
            },
        )
        assert resp.status_code == 400
        assert "ambiguous" in resp.json()["detail"]
        assert session.sent == []
    finally:
        client.__exit__(None, None, None)