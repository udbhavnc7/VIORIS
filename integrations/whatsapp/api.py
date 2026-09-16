"""
WhatsApp Connector Service (Phase 6, Prompt 6.2).

FastAPI surface for the WhatsApp connector: link a browser session, fetch the
digest, and — gated at Execute tier with an approved ledger entry — send a
message. The digest endpoint is Observe-tier; the send endpoint requires
`whatsapp.send_message` to be registered at EXECUTE and verifies an approved
approval in the task-runner ledger before anything is transmitted. Tokens stay
in the encrypted vault.

Run:
    uvicorn integrations.whatsapp.api:app --port 8446
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from packages.shared.permission_engine import (
    PermissionEngine,
    UnknownToolError,
    register_phase6_connector_tools,
)

from ..base import (
    ConnectionMissingError,
    ConnectorError,
    ExpiredSessionError,
    PermissionNotApprovedError,
    RateLimitError,
)
from ..token_vault import TokenVault
from .connector import WhatsAppConnector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe reads + Execute send
    yield


app = FastAPI(title="Vioris WhatsApp Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: WhatsAppConnector | None = None


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> WhatsAppConnector:
    global _connector
    if _connector is None:
        _connector = WhatsAppConnector(get_vault())
    return _connector


def get_approval_store():
    """Open the task-runner's approval ledger (shared DB, same data dir)."""
    from services.task_runner.app.approvals import ApprovalStore

    base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
    return ApprovalStore(base / "task_runner.db")


def _approval_verifier(task_id: str | None, step_id: str | None, idempotency_key: str) -> Callable[[], bool]:
    def verify() -> bool:
        if not task_id or not step_id:
            return False
        return get_approval_store().is_approved_for_step(task_id, step_id, idempotency_key)

    return verify


class LinkStartRequest(BaseModel):
    session_name: str = "whatsapp-web"


class DigestResponse(BaseModel):
    account: str
    window_hours: int
    total: int
    flagged_hidden: int
    entries: list[dict]


class SendRequest(BaseModel):
    session_ref: str | None = None
    recipient: str
    content: str
    channel: str = "whatsapp"
    idempotency_key: str | None = None
    task_id: str | None = None
    step_id: str | None = None


class SendResponse(BaseModel):
    message_id: str
    recipient: str
    recipient_identity: str
    channel: str
    idempotency_key: str
    replay: bool


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "whatsapp-connector",
        "whatsapp_read_digest_observed": PermissionEngine.is_registered("whatsapp.read_digest"),
        "whatsapp_send_message_execute": PermissionEngine.is_registered("whatsapp.send_message"),
    }


@app.get("/sessions")
def list_sessions() -> list[dict]:
    sessions = get_vault().list("whatsapp")
    for s in sessions:
        s["permission_explanation"] = get_connector().permission_explanation
    return sessions


@app.post("/link/start")
def link_start(req: LinkStartRequest) -> dict:
    """Begin browser-session linking (returns a probing/polling handle)."""
    return get_connector().link_device()


@app.post("/link/complete")
def link_complete(session_ref: str) -> dict:
    """Persist the encrypted session reference after the user scans/pairs."""
    try:
        return get_connector().complete_link(session_ref)
    except (ConnectorError, ExpiredSessionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/digest")
def wa_digest(
    hours: int = Query(default=24, ge=1, le=168),
    max_messages: int = Query(default=40, ge=1, le=100),
    session_ref: str | None = None,
) -> DigestResponse:
    """Read-only recent-messages digest. Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data
    is fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("whatsapp.read_digest")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"whatsapp.read_digest is {result.tier.value}, refusing")

    conn = get_connector()
    sessions = get_vault().list("whatsapp")
    if not sessions:
        raise HTTPException(status_code=409, detail="no WhatsApp session linked; call /link/start first")
    ref = session_ref or sessions[0]["account"]
    try:
        digest = conn.fetch_digest(ref, hours=hours, max_messages=max_messages)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return DigestResponse(
        account=digest.account,
        window_hours=digest.window_hours,
        total=digest.total,
        flagged_hidden=digest.flagged_hidden,
        entries=[_entry_dict(e) for e in digest.entries],
    )


@app.post("/send")
def send(req: SendRequest) -> SendResponse:
    """Send a WhatsApp message. Execute-tier: only fires with an approved
    ledger entry for the caller's task+step+idempotency_key.

    Permission gate: `whatsapp.send_message` must be registered at EXECUTE —
    an unregistered tool, or one sitting at a lower tier, is refused before
    any content is touched. The connector ALSO verifies the approval ledger
    and refuses without an approved record. No shortcut, no silent send.
    """
    if not req.idempotency_key:
        raise HTTPException(status_code=422, detail="idempotency_key is required for a send")
    try:
        result = PermissionEngine.classify("whatsapp.send_message")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "execute":
        raise HTTPException(status_code=403, detail=f"whatsapp.send_message is {result.tier.value}, refusing")

    conn = get_connector()
    sessions = get_vault().list("whatsapp")
    if not sessions:
        raise HTTPException(status_code=409, detail="no WhatsApp session linked; call /link/start first")
    ref = req.session_ref or sessions[0]["account"]
    try:
        sent = conn.send_message(
            ref,
            recipient=req.recipient,
            content=req.content,
            channel=req.channel,
            idempotency_key=req.idempotency_key,
            approval_verifier=_approval_verifier(req.task_id, req.step_id, req.idempotency_key),
        )
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except PermissionNotApprovedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SendResponse(
        message_id=sent.message_id,
        recipient=sent.recipient,
        recipient_identity=sent.recipient_identity,
        channel=sent.channel,
        idempotency_key=sent.idempotency_key,
        replay=sent.replay,
    )


@app.post("/unlink/{session_ref}")
def unlink(session_ref: str) -> dict:
    conn = get_connector()
    try:
        conn.unlink_session(session_ref)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "whatsapp", "session_ref": session_ref, "unlinked": True}


def _entry_dict(entry: Any) -> dict:
    return {
        "sender": entry.sender,
        "summary": entry.summary,
        "ask": entry.ask,
        "message_id": entry.message_id,
        "kind": entry.kind,
        "inaccessible_reason": entry.inaccessible_reason,
        "contains_request_words": entry.contains_request_words,
    }