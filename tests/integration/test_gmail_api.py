"""
Integration tests for the Phase 6 Gmail connector service per Prompt 6.1.

Boots the FastAPI app against the mock transport so the full HTTP surface
(connect → digest → status → disconnect) runs with zero network, and asserts
the endpoint falls back to the permission engine's static classification.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import integrations.gmail.api as gmail_api
from integrations.mock_providers.gmail_mock import MockGmailTransport
from packages.shared.permission_engine import (
    PermissionEngine,
    RiskTier,
    register_phase6_connector_tools,
)


def _make_client(transport: MockGmailTransport) -> TestClient:
    # isolate the token vault + connector in a temp dir; swap transport for mock
    gmail_api._vault = None
    gmail_api._connector = None
    gmail_api._write_connector = None
    os.environ["VIORUS_DATA_DIR"] = tempfile.mkdtemp(prefix="vioris-gmail-test-")

    # Other test modules (e.g. test_task_stop_all) freeze the registry and
    # never reset it. Make this file deterministic: fresh, unfrozen registry
    # with the phase-6 tools registered so the lifespan can't double-register.
    PermissionEngine.reset()
    register_phase6_connector_tools()

    def _patched_get_connector():
        from integrations.gmail.connector import GmailConnector

        return GmailConnector(gmail_api.get_vault(), transport=transport)

    def _patched_get_write_connector():
        from integrations.gmail.write_connector import GmailWriteConnector

        return GmailWriteConnector(gmail_api.get_vault(), transport=transport)

    gmail_api.get_connector = _patched_get_connector  # type: ignore[assignment]
    gmail_api.get_write_connector = _patched_get_write_connector  # type: ignore[assignment]
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
        assert resp.json()["gmail_react_execute"] is True
        assert resp.json()["gmail_send_reply_execute"] is True
    finally:
        client.__exit__(None, None, None)


# ── write path (Execute-tier, approval-ledger-gated) ─────────────────────────


def _connect_write(client: TestClient) -> dict:
    """Opt in to write scopes, then complete the mock OAuth exchange."""
    start = client.post("/connect/start", json={"include_write": True})
    assert start.status_code == 200
    state = start.json()["state"]
    done = client.post(f"/connect/complete?state={state}&code=writecode")
    assert done.status_code == 200
    return done.json()


def _approve(tool: str, task_id: str, step_id: str, key: str, args: dict) -> None:
    """Write an approved record into the ledger the verifier reads."""
    from pathlib import Path

    from services.task_runner.app.approvals import ApprovalStore

    store = ApprovalStore(Path(os.environ["VIORUS_DATA_DIR"]) / "task_runner.db")
    approval = store.create(task_id, step_id, key, tool, args)
    store.approve(approval)


def test_connect_opt_in_write_reports_write_scopes() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        summary = _connect_write(client)
        assert "https://www.googleapis.com/auth/gmail.modify" in summary["scopes"]
        assert "https://www.googleapis.com/auth/gmail.send" in summary["scopes"]

        accounts = client.get("/accounts").json()
        assert "https://www.googleapis.com/auth/gmail.send" in accounts[0]["scopes"]
        assert "replies" in accounts[0]["permission_explanation"].lower()
    finally:
        client.__exit__(None, None, None)


def test_react_requires_idempotency_key() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        _connect_write(client)
        resp = client.post("/react", json={"message_id": "m1"})
        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_react_blocked_when_tool_unregistered() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        _connect_write(client)
        PermissionEngine._registry.pop("gmail.react", None)
        resp = client.post(
            "/react",
            json={"message_id": "m1", "idempotency_key": "k-x"},
        )
        assert resp.status_code == 403
        assert transport.modified == []  # nothing touched the mailbox

        from packages.shared.schemas import ToolRegistration

        PermissionEngine.register(
            ToolRegistration(
                tool_name="gmail.react",
                tier=RiskTier.EXECUTE,
                confirmation_required=True,
                description="restored",
                diff_card_fields=["message_id", "reaction", "label"],
            )
        )
    finally:
        client.__exit__(None, None, None)


def test_react_refused_without_approved_ledger() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        _connect_write(client)
        resp = client.post(
            "/react",
            json={
                "message_id": "m1",
                "reaction": "acknowledge",
                "idempotency_key": "k-react-y",
                "task_id": "task-y",
                "step_id": "step-y",
            },
        )
        assert resp.status_code == 403
        assert "no approved approval" in resp.json()["detail"]
        assert transport.modified == []
    finally:
        client.__exit__(None, None, None)


def test_react_fires_with_approved_ledger_and_is_idempotent() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        _connect_write(client)
        _approve(
            "gmail.react",
            "task-react",
            "step-react",
            "k-react-live",
            {"message_id": "m1", "reaction": "acknowledge"},
        )
        payload = {
            "message_id": "m1",
            "reaction": "acknowledge",
            "idempotency_key": "k-react-live",
            "task_id": "task-react",
            "step_id": "step-react",
        }
        first = client.post("/react", json=payload)
        assert first.status_code == 200
        body = first.json()
        assert body["replay"] is False
        assert body["added_labels"] == ["STARRED"]
        assert body["removed_labels"] == ["UNREAD"]
        assert len(transport.modified) == 1

        second = client.post("/react", json=payload)
        assert second.status_code == 200
        assert second.json()["replay"] is True
        assert len(transport.modified) == 1  # a retry never double-applies
    finally:
        client.__exit__(None, None, None)


def test_reply_requires_idempotency_key() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        _connect_write(client)
        resp = client.post(
            "/reply",
            json={
                "thread_id": "t1",
                "to": "boss@example.com",
                "subject": "Re: Interview",
                "body": "Tuesday works.",
            },
        )
        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_reply_refused_without_approved_ledger() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        _connect_write(client)
        resp = client.post(
            "/reply",
            json={
                "thread_id": "t1",
                "to": "boss@example.com",
                "subject": "Re: Interview",
                "body": "Tuesday works.",
                "idempotency_key": "k-reply-y",
                "task_id": "task-y",
                "step_id": "step-y",
            },
        )
        assert resp.status_code == 403
        assert transport.sent == []
    finally:
        client.__exit__(None, None, None)


def test_reply_fires_with_approved_ledger_and_is_idempotent() -> None:
    transport = MockGmailTransport()
    client = _make_client(transport)
    try:
        _connect_write(client)
        _approve(
            "gmail.send_reply",
            "task-reply",
            "step-reply",
            "k-reply-live",
            {
                "thread_id": "t1",
                "to": "boss@example.com",
                "subject": "Re: Interview",
                "body": "Tuesday works.",
            },
        )
        payload = {
            "thread_id": "t1",
            "to": "boss@example.com",
            "subject": "Re: Interview",
            "body": "Tuesday works.",
            "idempotency_key": "k-reply-live",
            "task_id": "task-reply",
            "step_id": "step-reply",
        }
        first = client.post("/reply", json=payload)
        assert first.status_code == 200
        body = first.json()
        assert body["replay"] is False
        assert body["message_id"] == "sent_1"
        assert len(transport.sent) == 1

        second = client.post("/reply", json=payload)
        assert second.status_code == 200
        assert second.json()["replay"] is True
        assert second.json()["message_id"] == body["message_id"]
        assert len(transport.sent) == 1
    finally:
        client.__exit__(None, None, None)


# ── real-account wiring: OAuth callback + env resolution ────────────────────


def test_oauth_callback_completes_connection_and_hides_secrets() -> None:
    transport = MockGmailTransport()
    transport._auth_scope = "https://www.googleapis.com/auth/gmail.readonly"
    client = _make_client(transport)
    try:
        start = client.post("/connect/start", json={})
        assert start.status_code == 200
        state = start.json()["state"]

        callback = client.get(f"/oauth/authorized?state={state}&code=mockcode")
        assert callback.status_code == 200
        assert "Gmail connected" in callback.text
        assert "mock-user@gmail.com" in callback.text
        assert "mockcode" not in callback.text
        assert "client_secret" not in callback.text.lower()

        assert client.get("/accounts").json()[0]["account"] == "mock-user@gmail.com"
        assert client.get("/digest").status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_oauth_callback_reports_provider_error_honestly() -> None:
    client = _make_client(MockGmailTransport())
    try:
        callback = client.get("/oauth/authorized?error=access_denied")
        assert callback.status_code == 200
        assert "not connected" in callback.text
        assert "access_denied" in callback.text
        assert client.get("/accounts").json() == []
    finally:
        client.__exit__(None, None, None)


def test_oauth_callback_rejects_missing_code_or_state() -> None:
    client = _make_client(MockGmailTransport())
    try:
        assert client.get("/oauth/authorized").status_code == 400
        assert client.get("/oauth/authorized?code=only-code").status_code == 400
        assert client.get("/oauth/authorized?state=only-state").status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_credentials_prefer_vioris_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIORIS_GMAIL_CLIENT_ID", "canonical-id")
    monkeypatch.setenv("VIORIS_GMAIL_CLIENT_SECRET", "canonical-secret")
    monkeypatch.setenv("VIORUS_GMAIL_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("VIORUS_GMAIL_CLIENT_SECRET", "legacy-secret")
    assert gmail_api._credentials() == ("canonical-id", "canonical-secret")


def test_data_dir_prefers_vioris_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIORIS_DATA_DIR", "canonical-data")
    monkeypatch.setenv("VIORUS_DATA_DIR", "legacy-data")
    assert gmail_api._data_dir().name == "canonical-data"


def test_credentials_fall_back_to_gitignored_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for name in (
        "VIORIS_GMAIL_CLIENT_ID",
        "VIORIS_GMAIL_CLIENT_SECRET",
        "VIORUS_GMAIL_CLIENT_ID",
        "VIORUS_GMAIL_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIORIS_DATA_DIR", str(tmp_path))
    (tmp_path / "gmail_credentials.json").write_text(
        json.dumps({"client_id": "file-id", "client_secret": "file-secret"}),
        encoding="utf-8",
    )
    assert gmail_api._credentials() == ("file-id", "file-secret")


def test_corrupt_credentials_file_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for name in (
        "VIORIS_GMAIL_CLIENT_ID",
        "VIORIS_GMAIL_CLIENT_SECRET",
        "VIORUS_GMAIL_CLIENT_ID",
        "VIORUS_GMAIL_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIORIS_DATA_DIR", str(tmp_path))
    (tmp_path / "gmail_credentials.json").write_text("{not json", encoding="utf-8")
    assert gmail_api._credentials() == ("", "")