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
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger("vioris.gateway")

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
COMPUTER_URL = os.getenv("VIORUS_COMPUTER_URL", "http://127.0.0.1:8430")
GATEWAY_HOST = os.getenv("VIORIS_API_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("VIORIS_API_PORT", "8420"))

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

_bg_task: asyncio.Task | None = None


async def _digest_checker() -> None:
    """Background loop: checks digest triggers every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        try:
            pipeline = get_pipeline()
            call_id = await pipeline.tick()
            if call_id:
                logger.info("Background digest triggered: call %s", call_id)
        except Exception as exc:
            logger.warning("Digest checker error: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase1_tools()
    from packages.shared.permission_engine import PermissionEngine
    PermissionEngine.freeze()

    # Start background digest checker
    global _bg_task
    _bg_task = asyncio.create_task(_digest_checker())
    logger.info("Background digest checker started")

    yield

    # Shutdown
    if _bg_task:
        _bg_task.cancel()
        try:
            await _bg_task
        except asyncio.CancelledError:
            pass


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

from services.api.app.websocket import WebSocketHub, WSMessage

_hub: WebSocketHub | None = None
_pipeline = None  # ProactiveCallPipeline, lazy init


def get_hub() -> WebSocketHub:
    global _hub
    if _hub is None:
        _hub = WebSocketHub()
    return _hub


def get_pipeline():
    """Lazy-init the proactive call pipeline."""
    global _pipeline
    if _pipeline is None:
        from packages.shared.proactive_pipeline import ProactiveCallPipeline

        _pipeline = ProactiveCallPipeline(hub=get_hub())
        _setup_hub_handlers(get_hub(), _pipeline)
    return _pipeline


def _setup_hub_handlers(hub: WebSocketHub, pipeline) -> None:
    """Register message handlers on the hub for incoming phone commands."""

    async def handle_voice(msg: WSMessage) -> None:
        """Phone sent a voice utterance during an active call."""
        call_id = msg.payload.get("call_id", "")
        text = msg.payload.get("text", "")
        if not call_id or not text:
            return
        result = await pipeline.handle_phone_message(call_id, text)
        # Response is already pushed by handle_phone_message

    async def handle_call_accept(msg: WSMessage) -> None:
        """Phone accepted the ring."""
        call_id = msg.payload.get("call_id", "")
        if not call_id:
            return
        spoken = await pipeline.handle_call_accept(call_id)
        if spoken:
            await hub.send_to_phones(
                {
                    "type": "digest",
                    "payload": {
                        "call_id": call_id,
                        "text": spoken,
                        "items": [
                            i.to_dict()
                            for i in (pipeline.get_session(call_id).digest.items if pipeline.get_session(call_id) and pipeline.get_session(call_id).digest else [])
                        ],
                    },
                }
            )

    async def handle_call_end(msg: WSMessage) -> None:
        """Phone ended the call."""
        call_id = msg.payload.get("call_id", "")
        session = pipeline.get_session(call_id)
        if session:
            from packages.shared.proactive_pipeline import DigestStatus
            session.status = DigestStatus.COMPLETED

    async def handle_approve(msg: WSMessage) -> None:
        """Phone approved an action."""
        call_id = msg.payload.get("call_id", "")
        action_id = msg.payload.get("action_id", "")
        result = await pipeline.handle_phone_message(call_id, "approve")
        # Action execution happens via pipeline.on_action callback

    async def handle_reject(msg: WSMessage) -> None:
        """Phone rejected an action."""
        call_id = msg.payload.get("call_id", "")
        await pipeline.handle_phone_message(call_id, "no don't")

    hub.on("voice", handle_voice)
    hub.on("call_accept", handle_call_accept)
    hub.on("call_end", handle_call_end)
    hub.on("approve", handle_approve)
    hub.on("reject", handle_reject)


@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket) -> None:
    """Device channel: the phone presents its device JWT in the query param.

    Once authenticated, the connection is bidirectional:
    - Phone can send commands (voice, approve, reject, status)
    - Laptop can push events (ring, digest, approval_request)
    """
    token = websocket.query_params.get("token")
    device_type = websocket.query_params.get("type", "phone")

    # Verify JWT
    device = None
    if token:
        try:
            device = get_store().verify(token)
        except (InvalidDeviceTokenError, DeviceRevokedError):
            pass

    client_id = device.device_id if device else f"anon-{id(websocket)}"
    hub = get_hub()

    try:
        client = await hub.connect(
            websocket,
            client_id=client_id,
            device_type=device_type,
            device_id=device.device_id if device else None,
            authenticated=device is not None,
        )

        # Listen for messages from the phone
        while True:
            try:
                raw = await websocket.receive_text()
                await hub.receive(client_id, raw)
            except WebSocketDisconnect:
                break
    finally:
        await hub.disconnect(client_id)


# ─── push endpoints (laptop → phone) ─────────────────────────────────────────


@app.post("/v1/push/ring")
async def push_ring(
    payload: dict,
    device: Device = Depends(_require_device),
) -> dict:
    """Push a ring event to all authenticated phones."""
    hub = get_hub()
    sent = await hub.send_to_phones(
        {
            "type": "ring",
            "payload": {
                "source": payload.get("source", "proactive"),
                "caller_name": payload.get("caller_name", "Vioris"),
                "message": payload.get("message", ""),
                "ring_id": payload.get("ring_id", ""),
            },
        }
    )
    return {"pushed_to": len(sent), "device": device.device_id}


@app.post("/v1/push/digest")
async def push_digest(
    payload: dict,
    device: Device = Depends(_require_device),
) -> dict:
    """Push a digest summary to all authenticated phones."""
    hub = get_hub()
    sent = await hub.send_to_phones(
        {
            "type": "digest",
            "payload": {
                "title": payload.get("title", "Daily Digest"),
                "items": payload.get("items", []),
                "spoken_text": payload.get("spoken_text", ""),
            },
        }
    )
    return {"pushed_to": len(sent), "device": device.device_id}


@app.post("/v1/push/approval")
async def push_approval(
    payload: dict,
    device: Device = Depends(_require_device),
) -> dict:
    """Push an approval request to all authenticated phones."""
    hub = get_hub()
    sent = await hub.send_to_phones(
        {
            "type": "approval_request",
            "payload": {
                "action_id": payload.get("action_id", ""),
                "tool": payload.get("tool", ""),
                "description": payload.get("description", ""),
                "risk_level": payload.get("risk_level", ""),
                "diff_summary": payload.get("diff_summary", ""),
            },
        }
    )
    return {"pushed_to": len(sent), "device": device.device_id}


@app.post("/v1/push/text")
async def push_text(
    payload: dict,
    device: Device = Depends(_require_device),
) -> dict:
    """Push a generic text message to all authenticated phones."""
    hub = get_hub()
    sent = await hub.send_to_phones(
        {
            "type": payload.get("message_type", "text"),
            "payload": payload.get("payload", {}),
        }
    )
    return {"pushed_to": len(sent), "device": device.device_id}


@app.get("/v1/devices/connected")
async def connected_devices(device: Device = Depends(_require_device)) -> dict:
    """List currently connected WebSocket clients."""
    hub = get_hub()
    return {
        "device": device.device_id,
        "phones": len(hub.get_phone_clients()),
        "laptops": len(hub.get_laptop_clients()),
        "total": hub.connected_count,
    }


# ─── proactive call endpoints ────────────────────────────────────────────────


@app.post("/v1/call/trigger")
async def trigger_call(device: Device = Depends(_require_device)) -> dict:
    """Manually trigger a proactive call to all connected phones."""
    pipeline = get_pipeline()
    call_id = await pipeline.tick()
    if call_id:
        return {
            "device": device.device_id,
            "call_id": call_id,
            "status": "ringing",
            "note": "Ring pushed to all authenticated phones",
        }
    return {
        "device": device.device_id,
        "call_id": None,
        "status": "no_trigger",
        "note": "Digest schedule did not trigger (wrong hour or too recent)",
    }


@app.post("/v1/call/{call_id}/speak")
async def speak_digest(
    call_id: str, device: Device = Depends(_require_device)
) -> dict:
    """Trigger the digest to be spoken (phone accepted the call)."""
    pipeline = get_pipeline()
    spoken = await pipeline.handle_call_accept(call_id)
    if spoken:
        return {
            "device": device.device_id,
            "call_id": call_id,
            "spoken_text": spoken,
            "status": "active",
        }
    return {"device": device.device_id, "call_id": call_id, "status": "not_found"}


@app.post("/v1/call/{call_id}/message")
async def call_message(
    call_id: str, payload: dict, device: Device = Depends(_require_device)
) -> dict:
    """Send a voice utterance from the phone during an active call."""
    pipeline = get_pipeline()
    text = payload.get("text", "")
    result = await pipeline.handle_phone_message(call_id, text)
    return {"device": device.device_id, "call_id": call_id, **result}


@app.get("/v1/call/{call_id}/status")
async def call_status(
    call_id: str, device: Device = Depends(_require_device)
) -> dict:
    """Get the status of a call session."""
    pipeline = get_pipeline()
    session = pipeline.get_session(call_id)
    if not session:
        return {"device": device.device_id, "call_id": call_id, "status": "not_found"}
    return {
        "device": device.device_id,
        "call_id": call_id,
        "status": session.status.value,
        "turns": len(session.turns),
        "items_count": len(session.digest.items) if session.digest else 0,
        "pending_actions": len(session.pending_actions),
    }


@app.get("/v1/calls")
async def list_calls(device: Device = Depends(_require_device)) -> dict:
    """List all call sessions."""
    pipeline = get_pipeline()
    sessions = pipeline.get_all_sessions()
    return {
        "device": device.device_id,
        "calls": [
            {
                "call_id": s.call_id,
                "status": s.status.value,
                "source": s.source.value,
                "turns": len(s.turns),
                "created_at": s.created_at,
            }
            for s in sessions
        ],
    }


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


@app.get("/phone", response_class=HTMLResponse)
async def phone_page() -> FileResponse:
    """Serve the phone call UI. Pass ?token=JWT for WebSocket auth."""
    return FileResponse(_STATIC_DIR / "phone.html")