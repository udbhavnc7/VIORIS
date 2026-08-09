"""
Vioris API gateway — FastAPI entry point (Phase 1 skeleton → Phase 5, Prompt 5.1).

Phase 5 adds device pairing: short-lived QR-encoded pairing tokens, exchange
for a device-scoped JWT, protected WebSocket, and a devices list + revoke
endpoint. Permission registry is still registered + frozen at startup.

Run from the repo root:
    uvicorn services.api_gateway.app.main:app --reload --port 8420
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase1_tools,
)

from .pairing import (
    DeviceRevokedError,
    ExchangedToken,
    InvalidDeviceTokenError,
    InvalidPairingTokenError,
    PairingExpiredError,
    PairingStore,
)

_store: PairingStore | None = None


def get_store() -> PairingStore:
    global _store
    if _store is None:
        _store = PairingStore()
    return _store


# ─── models ──────────────────────────────────────────────────────────────────


class PairRequest(BaseModel):
    """Start pairing: the user names the device they are about to pair."""

    name: str = Field(min_length=1, max_length=60)


class ExchangeRequest(BaseModel):
    """Second slice: the phone sends the scanned token + its device id."""

    device_id: str
    token: str


class DeviceResponse(BaseModel):
    device_id: str
    name: str
    created_at: str
    revoked: bool


def _device_dict(device) -> DeviceResponse:
    return DeviceResponse(
        device_id=device.device_id,
        name=device.name,
        created_at=device.created_at,
        revoked=device.revoked,
    )


# ─── lifespan ────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase1_tools()
    from packages.shared.permission_engine import PermissionEngine

    PermissionEngine.freeze()
    yield


app = FastAPI(title="Vioris API Gateway", version="0.2.0", lifespan=lifespan)


# ─── pairing endpoints ───────────────────────────────────────────────────────


@app.post("/auth/device/pair")
async def pair(req: PairRequest) -> dict:
    proof = get_store().issue_pairing(req.name)
    return {
        "device_id": proof.device_id,
        "name": req.name,
        "pairing_token": proof.token,  # QR-encode this; never echo to a server
        "expires_at": proof.expires_at,
        "note": "scan the token on the phone within 10 minutes, then call /auth/device/exchange",
    }


@app.post("/auth/device/exchange")
async def exchange(req: ExchangeRequest) -> dict:
    try:
        exchanged = get_store().exchange(req.device_id, req.token)
    except InvalidPairingTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PairingExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except DeviceRevokedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "device_id": exchanged.device_id,
        "jwt": exchanged.jwt,
        "expires_at": exchanged.expires_at,
        "token_type": "bearer",
    }


@app.get("/devices")
async def list_devices() -> list[dict]:
    return [_device_dict(d).model_dump() for d in get_store().list_devices()]


@app.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: str) -> dict:
    ok = get_store().revoke(device_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"unknown device '{device_id}'")
    return {"device_id": device_id, "revoked": True}


# ─── device-authenticated channel (WebSocket) ────────────────────────────────


@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket) -> None:
    """Device channel: the phone must present its device JWT. Pairing/revokation
    is the only gating before this socket is accepted."""
    await websocket.accept()
    token = websocket.query_params.get("token")
    try:
        device = get_store().verify(token or "")
    except InvalidDeviceTokenError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return
    except DeviceRevokedError:
        await websocket.close(code=4403, reason="device revoked")
        return

    try:
        while True:
            await websocket.send_json({"device_id": device.device_id, "status": "connected"})
            await asyncio.sleep(300)
    except WebSocketDisconnect:  # pragma: no cover - client closed
        return


# ─── existing endpoints ──────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    from packages.shared.permission_engine import PermissionEngine

    return {
        "status": "ok",
        "service": "api-gateway",
        "registry_frozen": PermissionEngine._frozen,
        "tools_registered": len(PermissionEngine.list_tools()),
        "paired_devices": len(get_store().list_devices()),
        "pairing_enabled": True,
    }


@app.get("/audit-events")
async def list_audit_events(limit: int = 100) -> dict:
    """Placeholder: real audit listing hits Postgres once the task-runner lands.

    Phase 1 only persists audit events in the local agent store; this endpoint
    documents the contract from docs/02-architecture.md (GET /audit-events).
    """
    return {"events": [], "limit": limit, "note": "DB-backed listing lands with the task-runner"}


@app.get("/")
async def root() -> dict:
    return {"service": "vioris-api-gateway", "docs": "/docs"}