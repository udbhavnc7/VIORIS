"""
Vioris API gateway — FastAPI entry point (Phase 1 skeleton → Phase 5, Prompt 5.1).

Phase 5 adds device pairing: short-lived QR-encoded pairing tokens, exchange
for a device-scoped JWT, protected WebSocket, and a devices list + revoke
endpoint. Permission registry is still registered + frozen at startup.

Run from the repo root:
    uvicorn services.api_gateway.app.main:app --reload --port 8420

Device-scoped proxy: the mobile app talks ONLY to this gateway, which proxies
task/approval actions to the local task-runner (VIORUS_TASK_RUNNER_URL,
default http://127.0.0.1:8421). Every proxy call requires a verified device
JWT — the frontend never calls the task-runner directly.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from packages.shared.permission_engine import register_phase1_tools

from .pairing import (
    Device,
    DeviceRevokedError,
    InvalidDeviceTokenError,
    InvalidPairingTokenError,
    PairingExpiredError,
    PairingStore,
)

TASK_RUNNER_URL = os.getenv("VIORUS_TASK_RUNNER_URL", "http://127.0.0.1:8421")

_store: PairingStore | None = None

# ─── static files / dashboard ────────────────────────────────────────────────
_STATIC_DIR = Path(__file__).with_name("static")


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


class RemoteStartRequest(BaseModel):
    """Phone requests an approved remote session (Phase 5.3)."""

    name: str = Field(default="phone", min_length=1, max_length=60)
    timeout_minutes: int = Field(default=5, ge=1, le=60)


class RemoteInputRequest(BaseModel):
    """One keyboard/mouse action inside a live remote session."""

    session_id: str
    action: str = Field(description="type | left | right | move")
    text: str | None = None
    x: int | None = None
    y: int | None = None


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

# Serve the dashboard UI at /app
if _STATIC_DIR.exists():
    app.mount("/app", StaticFiles(directory=_STATIC_DIR, html=True), name="app")


def _require_device(authorization: str | None = Header(default=None)) -> Device:
    """FastAPI dependency: a verified, non-revoked device JWT."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return get_store().verify(token)
    except InvalidDeviceTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except DeviceRevokedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _proxy(method: str, path: str, device: Device, body: dict | None = None) -> dict:
    """Forward an authenticated device action to the local task-runner.

    The device JWT is verified here; the task-runner stays localhost-only and
    performs its own approval/audit recording for approve/reject/stop.
    """
    url = f"{TASK_RUNNER_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(method, url, json=body)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"task-runner unreachable: {exc}") from exc
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"raw": resp.text}
    if resp.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else str(payload)
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return payload


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


# ─── device-scoped proxy (mobile app talks only to this gateway) ─────────────


@app.get("/v1/tasks")
async def v1_tasks(device: Device = Depends(_require_device)) -> dict:
    return {"device": device.device_id, "tasks": await _proxy("GET", "/tasks", device)}


@app.get("/v1/approvals")
async def v1_approvals(device: Device = Depends(_require_device)) -> dict:
    return {"device": device.device_id, "approvals": await _proxy("GET", "/approvals", device)}


@app.post("/v1/approvals/{approval_id}/approve")
async def v1_approve(approval_id: str, device: Device = Depends(_require_device)) -> dict:
    return {
        "device": device.device_id,
        "result": await _proxy("POST", f"/approvals/{approval_id}/approve", device),
    }


@app.post("/v1/approvals/{approval_id}/reject")
async def v1_reject(approval_id: str, device: Device = Depends(_require_device)) -> dict:
    return {
        "device": device.device_id,
        "result": await _proxy("POST", f"/approvals/{approval_id}/reject", device),
    }


@app.post("/v1/stop")
async def v1_stop(device: Device = Depends(_require_device)) -> dict:
    """Stop Everything: halt every non-terminal task AND kill this device's
    remote sessions, so no further laptop input can flow. Both are audited by
    the task-runner / computer agent respectively."""
    try:
        stop = await _proxy("POST", "/stop-everything", device)
    except HTTPException:
        stop = {"stopped": 0, "note": "task-runner unreachable"}
    try:
        ended = await _computer_request(
            "POST", "/remote/session/end", {"device_id": device.device_id}
        )
    except HTTPException:
        ended = {"ended_sessions": 0, "note": "computer agent unreachable"}
    return {
        "device": device.device_id,
        "result": stop,
        "remote_sessions_ended": ended.get("ended_sessions", 0),
    }


@app.get("/v1/activity")
async def v1_activity(device: Device = Depends(_require_device)) -> dict:
    """Device-authenticated activity/audit timeline for the phone.

    The task-runner's audit endpoint returns {"events": [...], "chain_ok": ...};
    this gate re-wraps it with the acting device so the phone only ever talks
    to the gateway.
    """
    audit = await _proxy("GET", "/audit-events", device)
    return {"device": device.device_id, "events": audit.get("events", []), "chain_ok": audit.get("chain_ok", True)}


# ─── Phase 5 remote control (Prompt 5.3) ─────────────────────────────────────


COMPUTER_URL = os.getenv("VIORUS_COMPUTER_URL", "http://127.0.0.1:8430")


async def _computer_request(method: str, path: str, body: dict | None = None) -> dict:
    """Talk to the computer agent daemon. It is localhost-only and session-gated."""
    url = f"{COMPUTER_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(method, url, json=body)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"computer agent unreachable: {exc}") from exc
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"raw": resp.text}
    if resp.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else str(payload)
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return payload


async def _computer_request_bytes(method: str, path: str) -> bytes:
    """Fetch a binary payload (e.g. a PNG frame) from the computer agent."""
    url = f"{COMPUTER_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(method, url)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"computer agent unreachable: {exc}") from exc
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.content


async def _require_active_session(device: Device) -> dict:
    """Every screen-mirror/input action must belong to a LIVE session for the
    acting device — an expired/revoked/unknown session is a 403, never a retry."""
    active = await _computer_request("GET", f"/remote/session/active?device_id={device.device_id}")
    session = active.get("session")
    if session is None:
        raise HTTPException(status_code=403, detail="no active remote session for this device")
    return session


SCREENSHOT_MIN_INTERVAL_SECONDS = 2.0  # on-demand only; never a stream
_FRAME_LOCK: dict[str, float] = {}


async def _throttle_frame(device_id: str) -> None:
    """Enforce on-demand screen mirroring: at most one frame per device per
    interval. Mirroring is a peek (on demand), never a live stream."""
    import time

    now = time.monotonic()
    last = _FRAME_LOCK.get(device_id, 0.0)
    if now - last < SCREENSHOT_MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=429,
            detail="screen mirroring is on-demand only; wait a moment",
        )
    _FRAME_LOCK[device_id] = now


@app.post("/v1/remote/start")
async def v1_remote_start(req: RemoteStartRequest, device: Device = Depends(_require_device)) -> dict:
    """Phone asks to control this laptop. This creates an EXECUTE-tier task on
    the task-runner (computer.start_remote_session); the engine pauses at the
    approval gate, so the phone must approve the diff card before any input
    path opens. Nothing about the laptop is touched here."""
    task = await _proxy(
        "POST",
        "/tasks",
        device,
        {
            "request": f"start remote session for {req.name} (device {device.device_id})",
            "steps": [
                {
                    "agent": "computer",
                    "tool": "computer.start_remote_session",
                    "risk_level": "execute",
                    "result": {
                        "args": {
                            "device_id": device.device_id,
                            "timeout_minutes": req.timeout_minutes,
                        }
                    },
                }
            ],
        },
    )
    await _proxy("POST", f"/tasks/{task['task_id']}/run", device)
    return {"device": device.device_id, "task": task["task_id"], "note": "approval required before session starts"}


@app.get("/v1/remote/session")
async def v1_remote_session(device: Device = Depends(_require_device)) -> dict:
    """Current live session for this device (or null)."""
    active = await _computer_request("GET", f"/remote/session/active?device_id={device.device_id}")
    return {"device": device.device_id, "session": active.get("session")}


@app.get("/v1/remote/screenshot")
async def v1_remote_screenshot(device: Device = Depends(_require_device)) -> Response:
    """On-demand screen mirror frame, ONLY while a live session exists and at
    most one frame per device per interval — never a continuous stream. Returns
    raw PNG bytes the phone renders directly."""
    await _require_active_session(device)
    await _throttle_frame(device.device_id)
    data = await _computer_request_bytes("GET", f"/remote/screenshot-image?device_id={device.device_id}")
    return Response(content=data, media_type="image/png")


@app.post("/v1/remote/end")
async def v1_remote_end(device: Device = Depends(_require_device)) -> dict:
    """Phone explicitly ends its own remote session now (before auto-expiry).
    Immediately invalidates every live session the device holds."""
    return await _computer_request(
        "POST", "/remote/session/end", {"device_id": device.device_id}
    )


@app.post("/v1/remote/lock")
async def v1_remote_lock(device: Device = Depends(_require_device)) -> dict:
    """Remote 'lock now' from the phone. Execute-tier and session-gated: a live
    session for this device must exist, then the laptop locks immediately and
    the session ends so no further input can flow from the phone."""
    await _require_active_session(device)
    lock = await _computer_request(
        "POST", "/remote/lock", {"device_id": device.device_id}
    )
    lock["device"] = device.device_id
    return lock


@app.post("/v1/remote/input")
async def v1_remote_input(req: RemoteInputRequest, device: Device = Depends(_require_device)) -> dict:
    """Send keyboard/mouse input. The session gate is re-checked here AND at the
    computer daemon; every input is an execute-tier action in the static
    registry, but within a live, approved session the diff card was already
    shown for starting it."""
    await _require_active_session(device)
    return await _computer_request(
        "POST",
        "/remote/input",
        {
            "session_id": req.session_id,
            "device_id": device.device_id,
            "action": req.action,
            "text": req.text,
            "x": req.x,
            "y": req.y,
        },
    )


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
    return {"service": "vioris-api-gateway", "docs": "/docs", "dashboard": "/app"}


# ─── simple pairing page for the dashboard ──────────────────────────────────
@app.get("/pair", response_class=HTMLResponse)
async def pair_page() -> FileResponse:
    """Serve the pairing page (no auth required)."""
    return FileResponse(_STATIC_DIR / "pair.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> FileResponse:
    """Serve the main dashboard (requires auth via JWT in localStorage)."""
    return FileResponse(_STATIC_DIR / "dashboard.html")