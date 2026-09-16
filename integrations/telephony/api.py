"""
Telephony (Calling) Connector Service (Phase 6).

FastAPI surface for the calling connector. Two tools, kept strictly separate:

  - `telephony.prepare_call` (PREPARE): contact lookup + talking-point brief +
    draft script. Shown, never dialed. No approval, but gated at Prepare tier.
  - `telephony.start_call` (EXECUTE): native-dialer handoff. Only fires with an
    approved record in the task-runner ledger for the step's idempotency_key,
    and the diff card shows the RESOLVED contact identity.

V1 rule (docs/08): no server-initiated calls. `start_call` opens the native
dialer pre-filled; it never dials autonomously.

Run:
    uvicorn integrations.telephony.api:app --port 8453
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from packages.shared.permission_engine import (
    PermissionEngine,
    UnknownToolError,
    register_phase6_connector_tools,
)

from ..base import (
    ConnectorError,
    PermissionNotApprovedError,
)
from ..token_vault import TokenVault
from .connector import TelephonyConnector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Prepare + Execute tiers
    yield


app = FastAPI(title="Vioris Telephony Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: TelephonyConnector | None = None


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> TelephonyConnector:
    global _connector
    if _connector is None:
        _connector = TelephonyConnector(get_vault())
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


class PrepareRequest(BaseModel):
    recipient: str
    purpose: str = ""


class StartCallRequest(BaseModel):
    recipient: str
    script: str = ""
    idempotency_key: str
    task_id: str | None = None
    step_id: str | None = None


class BriefResponse(BaseModel):
    recipient: str
    recipient_identity: str
    phone: str
    purpose: str
    talking_points: list[str]
    script: str
    drafted: bool


class HandoffResponse(BaseModel):
    handoff_id: str
    recipient_identity: str
    phone: str
    channel: str
    idempotency_key: str
    replay: bool


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "telephony-connector",
        "telephony_prepare_call_prepare": PermissionEngine.is_registered("telephony.prepare_call"),
        "telephony_start_call_execute": PermissionEngine.is_registered("telephony.start_call"),
    }


@app.post("/prepare")
def prepare(req: PrepareRequest) -> BriefResponse:
    """Draft a call brief (contact lookup + talking points + script). PREPARE
    tier: shown, never dialed. Gated so an unregistered/mismatched tool is
    refused before anything is produced."""
    try:
        result = PermissionEngine.classify("telephony.prepare_call")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "prepare":
        raise HTTPException(status_code=403, detail=f"telephony.prepare_call is {result.tier.value}, refusing")

    try:
        brief = get_connector().prepare_call(req.recipient, purpose=req.purpose)
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return BriefResponse(
        recipient=brief.recipient,
        recipient_identity=brief.recipient_identity,
        phone=brief.phone,
        purpose=brief.purpose,
        talking_points=brief.talking_points,
        script=brief.script,
        drafted=brief.drafted,
    )


@app.post("/start")
def start(req: StartCallRequest) -> HandoffResponse:
    """Open the native dialer pre-filled for the recipient (v1 handoff). EXECUTE
    tier: only fires with an approved ledger entry for the step's
    idempotency_key. The diff card shows the RESOLVED contact identity."""
    if not req.idempotency_key:
        raise HTTPException(status_code=422, detail="idempotency_key is required for a call")
    try:
        result = PermissionEngine.classify("telephony.start_call")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "execute":
        raise HTTPException(status_code=403, detail=f"telephony.start_call is {result.tier.value}, refusing")

    try:
        handoff = get_connector().start_call(
            req.recipient,
            script=req.script,
            idempotency_key=req.idempotency_key,
            approval_verifier=_approval_verifier(req.task_id, req.step_id, req.idempotency_key),
        )
    except PermissionNotApprovedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return HandoffResponse(
        handoff_id=handoff.handoff_id,
        recipient_identity=handoff.recipient_identity,
        phone=handoff.phone,
        channel=handoff.channel,
        idempotency_key=handoff.idempotency_key,
        replay=handoff.replay,
    )
