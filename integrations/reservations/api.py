"""
Reservations (booking) Connector Service (Phase 6).

FastAPI surface for the booking connector: link a booking-provider browser
session, search available slots (Observe), and create a reservation (Execute,
gated by an approved ledger record). `reservations.create` only fires with an
approved approval in the task-runner ledger for the step's idempotency_key,
and the diff card shows the full venue/date/time/party/guest payload.

Run:
    uvicorn integrations.reservations.api:app --port 8454
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

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
from .connector import ReservationsConnector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe + Execute tiers
    yield


app = FastAPI(title="Vioris Reservations Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: ReservationsConnector | None = None


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> ReservationsConnector:
    global _connector
    if _connector is None:
        _connector = ReservationsConnector(get_vault())
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


class SearchRequest(BaseModel):
    venue: str
    date: str = ""
    party_size: int = 2
    session_ref: str | None = None


class CreateRequest(BaseModel):
    slot_id: str
    venue: str
    at: str
    party_size: int = 2
    guest_name: str
    idempotency_key: str
    session_ref: str | None = None
    task_id: str | None = None
    step_id: str | None = None


class SlotResponse(BaseModel):
    slot_id: str
    venue: str
    at: str
    party_size: int


class SearchResponse(BaseModel):
    venue: str
    date: str
    party_size: int
    total: int
    slots: list[SlotResponse]


class CreateResponse(BaseModel):
    reservation_id: str
    venue: str
    at: str
    party_size: int
    guest_name: str
    confirmation_note: str
    idempotency_key: str
    replay: bool


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "reservations-connector",
        "reservations_search_slots_observed": PermissionEngine.is_registered("reservations.search_slots"),
        "reservations_create_execute": PermissionEngine.is_registered("reservations.create"),
    }


@app.get("/sessions")
def list_sessions() -> list[dict]:
    sessions = get_vault().list("reservations")
    for s in sessions:
        s["permission_explanation"] = get_connector().permission_explanation
    return sessions


@app.post("/link/start")
def link_start() -> dict:
    return get_connector().link_device()


@app.post("/link/complete")
def link_complete(session_ref: str) -> dict:
    try:
        return get_connector().complete_link(session_ref)
    except (ConnectorError, ExpiredSessionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/search")
def search(
    venue: str = Query(min_length=1),
    date: str = Query(default=""),
    party_size: int = Query(default=2, ge=1, le=20),
    session_ref: str | None = None,
) -> SearchResponse:
    """Read-only slot search. Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data is
    fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("reservations.search_slots")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"reservations.search_slots is {result.tier.value}, refusing")

    conn = get_connector()
    sessions = get_vault().list("reservations")
    if not sessions:
        raise HTTPException(status_code=409, detail="no booking session linked; call /link/start first")
    ref = session_ref or sessions[0]["account"]
    try:
        result_obj = conn.search_slots(ref, venue=venue, date=date, party_size=party_size)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return SearchResponse(
        venue=result_obj.venue,
        date=result_obj.date,
        party_size=result_obj.party_size,
        total=result_obj.total,
        slots=[_slot_dict(s) for s in result_obj.slots],
    )


@app.post("/create")
def create(req: CreateRequest) -> CreateResponse:
    """Create a reservation. Execute-tier: only fires with an approved ledger
    entry for the caller's task+step+idempotency_key.

    Permission gate: `reservations.create` must be registered at EXECUTE — an
    unregistered tool, or one at a lower tier, is refused before any slot is
    touched. The connector ALSO verifies the approval ledger and refuses
    without an approved record. No shortcut, no silent booking.
    """
    if not req.idempotency_key:
        raise HTTPException(status_code=422, detail="idempotency_key is required for a reservation")
    try:
        result = PermissionEngine.classify("reservations.create")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "execute":
        raise HTTPException(status_code=403, detail=f"reservations.create is {result.tier.value}, refusing")

    conn = get_connector()
    sessions = get_vault().list("reservations")
    if not sessions:
        raise HTTPException(status_code=409, detail="no booking session linked; call /link/start first")
    ref = req.session_ref or sessions[0]["account"]
    try:
        res = conn.create_reservation(
            ref,
            slot_id=req.slot_id,
            venue=req.venue,
            at=req.at,
            party_size=req.party_size,
            guest_name=req.guest_name,
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

    return CreateResponse(
        reservation_id=res.reservation_id,
        venue=res.venue,
        at=res.at.isoformat(),
        party_size=res.party_size,
        guest_name=res.guest_name,
        confirmation_note=res.confirmation_note,
        idempotency_key=res.idempotency_key,
        replay=res.replay,
    )


@app.post("/unlink/{session_ref}")
def unlink(session_ref: str) -> dict:
    conn = get_connector()
    try:
        conn.unlink_session(session_ref)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "reservations", "session_ref": session_ref, "unlinked": True}


def _slot_dict(s: Any) -> dict:
    return {
        "slot_id": s.slot_id,
        "venue": s.venue,
        "at": s.at.isoformat(),
        "party_size": s.party_size,
    }
