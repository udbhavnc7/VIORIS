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
import base64
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("vioris.gateway")

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from packages.shared.kill_phrase import detect_kill_phrase
from packages.shared.permission_engine import PermissionEngine, register_all_tools
from packages.shared.second_factor import load_or_create_key, mint, pin_matches

from .pairing import (
    Device,
    DeviceRevokedError,
    InvalidDeviceTokenError,
    InvalidPairingTokenError,
    PairingExpiredError,
    PairingStore,
)

TASK_RUNNER_URL = os.getenv("VIORUS_TASK_RUNNER_URL") or os.getenv(
    "VIORIS_TASK_RUNNER_URL", "http://127.0.0.1:8421"
)
COMPUTER_URL = os.getenv("VIORUS_COMPUTER_URL") or os.getenv(
    "VIORIS_COMPUTER_URL", "http://127.0.0.1:8430"
)
BROWSER_URL = os.getenv("VIORUS_BROWSER_URL") or os.getenv(
    "VIORIS_BROWSER_URL", "http://127.0.0.1:8431"
)
DOCUMENTS_URL = os.getenv("VIORUS_DOCUMENTS_URL") or os.getenv(
    "VIORIS_DOCUMENTS_URL", "http://127.0.0.1:8432"
)
MEMORY_URL = os.getenv("VIORIS_MEMORY_URL") or os.getenv(
    "VIORUS_MEMORY_URL", "http://127.0.0.1:8441"
)
KNOWLEDGE_URL = os.getenv("VIORIS_KNOWLEDGE_URL") or os.getenv(
    "VIORUS_KNOWLEDGE_URL", "http://127.0.0.1:8442"
)
GATEWAY_HOST = os.getenv("VIORIS_API_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("VIORIS_API_PORT", "8420"))

_store: PairingStore | None = None

# ─── static files / dashboard ────────────────────────────────────────────────
_STATIC_DIR = Path(__file__).with_name("static")


def get_store() -> PairingStore:
    global _store
    if _store is None:
        # Persist devices + revocations + the signing secret across restarts.
        # The secret is generated once and stored beside the local database.
        store_path = os.getenv("VIORIS_PAIRING_STORE", "vioris_data/pairing.json")
        _store = PairingStore(store_path=store_path)
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


class CreateTaskPayload(BaseModel):
    """A natural-language request (or an explicit plan) from the desktop UI.

    When `steps` is omitted the task-runner asks the planner for a plan. Every
    step's risk tier is re-derived from the frozen registry — a caller can
    never downgrade an Execute step by labelling it "observe".
    """

    request: str = Field(min_length=1, max_length=2000)
    steps: list[dict] | None = None
    start: bool = True
    auto_run: bool = True


class KillPhrasePayload(BaseModel):
    transcript: str = ""


class MemoryCreatePayload(BaseModel):
    category: str
    content: str = Field(min_length=1)
    source: str = "user"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    user_opted_in: bool = False


class MemoryCorrectPayload(BaseModel):
    content: str = Field(min_length=1)
    category: str | None = None


class KnowledgeIngestPayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=1000)


class KnowledgeQueryPayload(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=20)


class ExplainQueryPayload(BaseModel):
    """A natural-language "why did you do that?" question."""

    query: str = Field(min_length=1, max_length=2000)


class CallTriggerPayload(BaseModel):
    """A user-defined reason for Vioris to place a proactive call.

    ``kind="time"`` needs ``at`` in HH:MM form (local time).
    ``kind="task_completed"`` needs a ``match`` substring for the task request.
    """

    kind: str = Field(description="time | task_completed")
    label: str = ""
    at: str | None = None
    match: str | None = None
    one_shot: bool = False


def _device_dict(device) -> DeviceResponse:
    return DeviceResponse(
        device_id=device.device_id,
        name=device.name,
        created_at=device.created_at,
        revoked=device.revoked,
    )


# ─── lifespan ────────────────────────────────────────────────────────────────

_bg_task: asyncio.Task | None = None
_event_bridge_task: asyncio.Task | None = None


def _task_runner_ws_url() -> str:
    base = TASK_RUNNER_URL.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :] + "/ws/events"
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :] + "/ws/events"
    return base + "/ws/events"


async def _forward_task_event(raw: str, hub=None) -> bool:
    """Push one task-runner wire event to both paired device classes.

    Every event type is forwarded: approvals, stops and kill-phrase events are
    exactly what "never act invisibly" requires the phone to see. Unknown or
    malformed events are ignored instead of being pushed as fake events.
    """
    import json

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(event, dict) or not event.get("type"):
        return False
    hub = hub or get_hub()
    await hub.send_to_phones(event)
    await hub.send_to_laptops(event)
    return True


async def _task_event_bridge() -> None:
    """Forward task-runner live events (approvals, stops) to paired devices."""
    import websockets

    backoff = 2.0
    while True:
        try:
            async with websockets.connect(_task_runner_ws_url(), open_timeout=3) as ws:
                backoff = 2.0
                async for raw in ws:
                    await _forward_task_event(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — retry quietly when runner is offline
            logger.debug("Task event bridge disconnected: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 15.0)


async def _completed_tasks_snapshot() -> list[dict]:
    """Read the task-runner's task list for trigger evaluation.

    Background polling cannot use ``_proxy`` because that requires a Device.
    This is the same localhost-only read the gateway already performs, just
    without a device in the loop.
    """
    try:
        resp = await _client().get(f"{TASK_RUNNER_URL}/tasks", timeout=5.0)
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    tasks = body.get("tasks") if isinstance(body, dict) else body
    return [task for task in tasks or [] if isinstance(task, dict)]


async def _digest_checker() -> None:
    """Background loop: checks digest and user call triggers every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        try:
            pipeline = get_pipeline()
            completed = await _completed_tasks_snapshot()
            call_id = await pipeline.tick(completed_tasks=completed)
            if call_id:
                logger.info("Background trigger started call %s", call_id)
        except Exception as exc:
            logger.warning("Digest checker error: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Register the COMPLETE static registry, then freeze it. A partial registry
    # makes planning refuse known tools and execution block them as unknown.
    if not PermissionEngine._frozen:
        register_all_tools()
        PermissionEngine.freeze()

    # Start background digest checker & task-runner event bridge
    global _bg_task, _event_bridge_task
    _bg_task = asyncio.create_task(_digest_checker())
    _event_bridge_task = asyncio.create_task(_task_event_bridge())
    logger.info("Background digest checker and task event bridge started")

    yield

    # Shutdown
    for task in (_bg_task, _event_bridge_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    if _pooled_client is not None and not _pooled_client.is_closed:
        await _pooled_client.aclose()


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


_pooled_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    """One pooled client for every local call the gateway makes.

    Constructing an AsyncClient builds an SSL context; on Windows that is a
    ~300 ms blocking operation. Creating one per request serialised the event
    loop and made ordinary polls take seconds, so all calls share this pool.
    """
    global _pooled_client
    if _pooled_client is None or _pooled_client.is_closed:
        # Keep-alive expiry is deliberately shorter than uvicorn's default
        # five-second idle close. Without this, a pooled socket can be reused
        # just as the server drops it and the read fails once.
        _pooled_client = httpx.AsyncClient(
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=2.0),
        )
    return _pooled_client


async def _proxy(
    method: str,
    path: str,
    device: Device,
    body: dict | None = None,
    headers: dict | None = None,
) -> dict:
    """Forward an authenticated device action to the local task-runner.

    The device JWT is verified here; the task-runner stays localhost-only and
    performs its own approval/audit recording for approve/reject/stop.
    """
    url = f"{TASK_RUNNER_URL}{path}"

    async def send() -> httpx.Response:
        return await _client().request(method, url, json=body, headers=headers, timeout=10.0)

    try:
        resp = await send()
    except httpx.RequestError as exc:
        # Read-only calls are safe to retry once if a keep-alive socket was
        # closed under us. Writes are never replayed.
        if method.upper() != "GET":
            logger.warning("proxy %s %s failed: %s", method, path, exc)
            raise HTTPException(status_code=503, detail=f"task-runner unreachable: {exc}") from exc
        try:
            resp = await send()
        except httpx.RequestError as retry_exc:
            logger.warning("proxy %s %s failed twice: %s", method, path, retry_exc)
            raise HTTPException(
                status_code=503, detail=f"task-runner unreachable: {retry_exc}"
            ) from retry_exc
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
async def list_devices(device: Device = Depends(_require_device)) -> list[dict]:
    return [_device_dict(d).model_dump() for d in get_store().list_devices()]


@app.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: str, device: Device = Depends(_require_device)) -> dict:
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
        from packages.shared.call_triggers import CallTriggerStore
        from packages.shared.proactive_pipeline import ProactiveCallPipeline

        _pipeline = ProactiveCallPipeline(
            hub=get_hub(), trigger_store=CallTriggerStore()
        )
        _setup_hub_handlers(get_hub(), _pipeline)
    return _pipeline


def _setup_hub_handlers(hub: WebSocketHub, pipeline) -> None:
    """Register message handlers on the hub for incoming phone commands."""

    from .call_audio import CallAudioBuffer, base64_decode, float32_to_pcm16_bytes
    from packages.shared.stt import WhisperSTT

    audio_buffers: dict[str, CallAudioBuffer] = {}

    stt_holder: list[WhisperSTT | None] = [None]

    def _get_stt() -> WhisperSTT:
        if stt_holder[0] is None:
            stt_holder[0] = WhisperSTT()
        return stt_holder[0]

    def _get_tts():
        from app.tts import get_tts

        return get_tts()

    async def _speak_and_push(call_id: str, result: dict) -> None:
        """Push the call response to the phone, with Piper TTS audio if we have a voice."""
        response_text = (result or {}).get("response_text")
        if response_text:
            await hub.send_to_phones({
                "type": "voice_response",
                "payload": {"call_id": call_id, "text": response_text},
            })
        if not response_text:
            return
        try:
            tts = _get_tts()
            for audio, rate in tts.synthesize(response_text):
                data = base64.b64encode(float32_to_pcm16_bytes(audio)).decode("ascii")
                await hub.send_to_phones({
                    "type": "tts",
                    "payload": {"call_id": call_id, "sample_rate": rate, "data": data},
                })
        except Exception as exc:  # noqa: BLE001 — TTS stays optional for now
            logger.warning("TTS synthesis unavailable (text still shown): %s", exc)

    async def handle_call_audio(msg: WSMessage) -> None:
        """Phone streams 16kHz mono base64 PCM while the user speaks."""
        call_id = str(msg.payload.get("call_id", ""))
        if not call_id:
            return
        buffer = audio_buffers.setdefault(call_id, CallAudioBuffer())
        audio = base64_decode(str(msg.payload.get("data", "")))
        if audio is not None:
            buffer.push(audio)

    async def handle_audio_end(msg: WSMessage) -> None:
        """User finished speaking: transcribe and feed the call pipeline."""
        call_id = str(msg.payload.get("call_id", ""))
        buffer = audio_buffers.pop(call_id, None)
        if not call_id or buffer is None or not buffer.has_speech():
            return
        text = await asyncio.to_thread(_get_stt().transcribe, buffer.drain())
        text = (text or "").strip()
        if not text:
            return
        result = await pipeline.handle_phone_message(call_id, text)
        await _speak_and_push(call_id, result)

    hub.on("call_audio", handle_call_audio)
    hub.on("audio_end", handle_audio_end)

    async def handle_voice(msg: WSMessage) -> None:
        """Phone sent a voice utterance during an active call."""
        call_id = msg.payload.get("call_id", "")
        if not call_id and pipeline:
            for sid, s in reversed(pipeline._sessions.items()):
                if s.status.value in ("ringing", "active"):
                    call_id = sid
                    break
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
        await pipeline.handle_call_end(call_id)

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
    if device_type not in ("phone", "laptop"):
        device_type = "phone"

    # Authentication is mandatory. An unauthenticated socket is never accepted:
    # accepting it would let any local process receive pushed digests/approvals
    # or drive handlers. Fail closed.
    if not token:
        await websocket.close(code=1008)
        return
    try:
        device = get_store().verify(token)
    except (InvalidDeviceTokenError, DeviceRevokedError):
        await websocket.close(code=1008)
        return

    client_id = device.device_id
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
async def trigger_call(
    payload: dict | None = None,
    device: Device = Depends(_require_device),
) -> dict:
    """Manually trigger a proactive call to all connected phones.

    Pass {"force": true} to bypass the digest schedule check.
    Pass {"demo": true} to use the scripted driving-scenario fixture.
    Production calls use whatever connectors are actually connected.
    """
    force = (payload or {}).get("force", False)
    demo = bool((payload or {}).get("demo", False))
    pipeline = get_pipeline()

    if force:
        # Bypass schedule — directly start a call
        import uuid
        call_id = str(uuid.uuid4())[:8]
        await pipeline._start_call(call_id, driving_scenario=demo)
    else:
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


@app.get("/v1/call/triggers")
async def list_call_triggers(device: Device = Depends(_require_device)) -> dict:
    """List user-defined proactive call triggers."""
    store = get_pipeline().trigger_store
    triggers = store.list() if store is not None else []
    return {
        "device": device.device_id,
        "triggers": [trigger.to_dict() for trigger in triggers],
    }


@app.post("/v1/call/triggers")
async def create_call_trigger(
    payload: CallTriggerPayload, device: Device = Depends(_require_device)
) -> dict:
    """Create a time or task-completion call trigger."""
    from packages.shared.call_triggers import TriggerValidationError

    store = get_pipeline().trigger_store
    if store is None:
        raise HTTPException(status_code=503, detail="call triggers are not configured")
    try:
        trigger = store.create(
            kind=payload.kind,
            label=payload.label,
            at=payload.at,
            match=payload.match,
            one_shot=payload.one_shot,
        )
    except TriggerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"device": device.device_id, "trigger": trigger.to_dict()}


@app.delete("/v1/call/triggers/{trigger_id}")
async def delete_call_trigger(
    trigger_id: str, device: Device = Depends(_require_device)
) -> dict:
    """Delete a call trigger. Missing ids are a 404, never a silent success."""
    store = get_pipeline().trigger_store
    if store is None or not store.delete(trigger_id):
        raise HTTPException(status_code=404, detail="trigger not found")
    return {
        "device": device.device_id,
        "trigger_id": trigger_id,
        "deleted": True,
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


@app.post("/v1/call/{call_id}/end")
async def end_call(
    call_id: str, device: Device = Depends(_require_device)
) -> dict:
    """End a call from a phone, even if its WebSocket is unavailable."""
    pipeline = get_pipeline()
    ended = await pipeline.handle_call_end(call_id)
    return {
        "device": device.device_id,
        "call_id": call_id,
        "status": "completed" if ended else "not_found",
    }


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
                # Sessions carry an epoch float internally; the UI consumes ISO
                # timestamps everywhere else, so normalise at the boundary.
                "created_at": datetime.fromtimestamp(s.created_at, UTC).isoformat(),
            }
            for s in sessions
        ],
    }


# ─── device-scoped proxy (mobile app talks only to this gateway) ─────────────


@app.get("/v1/tasks")
async def v1_tasks(device: Device = Depends(_require_device)) -> dict:
    return {"device": device.device_id, "tasks": await _proxy("GET", "/tasks", device)}


@app.post("/v1/tasks")
async def v1_create_task(
    req: CreateTaskPayload, device: Device = Depends(_require_device)
) -> dict:
    """Plan (or accept) a request, then advance it up to the next gate.

    The task-runner owns planning and execution. The gateway only: creates,
    optionally starts, and runs steps until the task pauses for approval,
    finishes, or fails. It never skips the approval gate.
    """
    # Kill phrase is checked before the request reaches the planner. This is
    # deliberately independent of the LLM and the approval flow: "stop
    # everything" must stop everything even if planning is confused.
    if detect_kill_phrase(req.request):
        killed = await _proxy(
            "POST", "/kill-phrase", device, {"transcript": req.request}
        )
        return {
            "device": device.device_id,
            "killed": True,
            "stopped": killed.get("stopped", []),
            "task": None,
            "task_id": None,
            "blocked_tools": [],
        }

    created = await _proxy(
        "POST",
        "/tasks",
        device,
        {"request": req.request, "steps": req.steps},
    )
    task_id = created.get("task_id")
    if not task_id:
        raise HTTPException(status_code=502, detail="task-runner returned no task id")

    result: dict = {"task": created, "task_id": task_id}
    if req.start:
        await _proxy("POST", f"/tasks/{task_id}/start", device)
    if req.auto_run:
        result = await _advance_task(device, task_id)
        result["task_id"] = task_id
    result["blocked_tools"] = created.get("blocked_tools", [])
    return {"device": device.device_id, **result}


@app.get("/v1/tasks/{task_id}")
async def v1_task(task_id: str, device: Device = Depends(_require_device)) -> dict:
    return {"device": device.device_id, "task": await _proxy("GET", f"/tasks/{task_id}", device)}


@app.get("/v1/tasks/{task_id}/explain")
async def v1_explain_task(task_id: str, device: Device = Depends(_require_device)) -> dict:
    """Why every step in a task was chosen (Phase 8.4)."""
    return {
        "device": device.device_id,
        "explanation": await _proxy("GET", f"/tasks/{task_id}/explain", device),
    }


@app.get("/v1/tasks/{task_id}/steps/{step_id}/explain")
async def v1_explain_step(
    task_id: str, step_id: str, device: Device = Depends(_require_device)
) -> dict:
    return {
        "device": device.device_id,
        "explanation": await _proxy(
            "GET", f"/tasks/{task_id}/steps/{step_id}/explain", device
        ),
    }


@app.post("/v1/explain")
async def v1_explain_query(
    req: ExplainQueryPayload, device: Device = Depends(_require_device)
) -> dict:
    """Answer a spoken/text "why did you do that?" from the local record."""
    return {
        "device": device.device_id,
        **(await _proxy("POST", "/explain", device, {"query": req.query})),
    }


@app.get("/v1/explain/recent")
async def v1_explain_recent(
    limit: int = 20, device: Device = Depends(_require_device)
) -> dict:
    return {
        "device": device.device_id,
        **(await _proxy("GET", f"/explain/recent?limit={limit}", device)),
    }


@app.post("/v1/tasks/{task_id}/run")
async def v1_run_task(task_id: str, device: Device = Depends(_require_device)) -> dict:
    return {"device": device.device_id, **(await _advance_task(device, task_id))}


@app.post("/v1/tasks/{task_id}/pause")
async def v1_pause_task(task_id: str, device: Device = Depends(_require_device)) -> dict:
    return {
        "device": device.device_id,
        "task": await _proxy("POST", f"/tasks/{task_id}/pause", device),
    }


@app.post("/v1/tasks/{task_id}/resume")
async def v1_resume_task(task_id: str, device: Device = Depends(_require_device)) -> dict:
    return {
        "device": device.device_id,
        "task": await _proxy("POST", f"/tasks/{task_id}/resume", device),
    }


@app.post("/v1/tasks/{task_id}/cancel")
async def v1_cancel_task(task_id: str, device: Device = Depends(_require_device)) -> dict:
    return {
        "device": device.device_id,
        "task": await _proxy("POST", f"/tasks/{task_id}/cancel", device),
    }


@app.post("/v1/tasks/{task_id}/retry/{step_id}")
async def v1_retry_step(
    task_id: str, step_id: str, device: Device = Depends(_require_device)
) -> dict:
    task = await _proxy("POST", f"/tasks/{task_id}/retry/{step_id}", device)
    return {"device": device.device_id, "task": task}


@app.get("/v1/approvals")
async def v1_approvals(device: Device = Depends(_require_device)) -> dict:
    approvals = await _proxy("GET", "/approvals", device)
    # The idempotency key and action hash are server-side consent bindings.
    # The UI renders the diff card; it never needs the capability values. The
    # risk tier is re-derived from the frozen registry (never the caller) and
    # attached so the approval card can warn and gate correctly.
    safe = []
    for approval in approvals:
        item = {k: v for k, v in approval.items() if k not in ("idempotency_key", "action_hash")}
        try:
            item["risk_level"] = PermissionEngine.classify(approval.get("tool")).tier.value
        except Exception:  # noqa: BLE001 — unknown tools are shown as critical
            item["risk_level"] = "critical"
        safe.append(item)
    return {"device": device.device_id, "approvals": safe}


CRITICAL_COOLDOWN_SECONDS = 5


def _parse_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        from datetime import UTC, datetime

        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except ValueError:
        return 0.0


@app.post("/v1/approvals/{approval_id}/approve")
async def v1_approve(
    approval_id: str,
    payload: dict | None = None,
    device: Device = Depends(_require_device),
) -> dict:
    """Approve exactly one action. Critical actions additionally require the
    locally configured PIN and the cooldown; the proof is bound to this
    approval id and verified again by the task-runner."""
    approvals = await _proxy("GET", "/approvals", device)
    target = next((a for a in approvals if a.get("approval_id") == approval_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="approval not found")

    try:
        from packages.shared.schemas import RiskTier

        tier = PermissionEngine.classify(target.get("tool")).tier
    except Exception as exc:  # noqa: BLE001 — unknown tool is never approvable
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    headers: dict | None = None
    if tier == RiskTier.CRITICAL:
        elapsed = time.time() - _parse_ts(target.get("created_at"))
        if elapsed < CRITICAL_COOLDOWN_SECONDS:
            retry_after = max(1, round(CRITICAL_COOLDOWN_SECONDS - elapsed))
            raise HTTPException(
                status_code=409,
                detail=f"Critical cooldown: wait {retry_after}s before approving.",
            )
        pin = (payload or {}).get("pin")
        ok, note = pin_matches(pin)
        if not ok:
            raise HTTPException(status_code=403, detail=note)
        headers = {"X-Vioris-Second-Factor": mint(approval_id, load_or_create_key())}

    approved = await _proxy(
        "POST", f"/approvals/{approval_id}/approve", device, headers=headers
    )
    task_id = approved.get("task_id")
    if not task_id:
        return {"device": device.device_id, "approval": approved, "result": approved}
    # Approval fires the armed step; keep running until the next gate/finish.
    advanced = await _advance_task(device, task_id)
    return {
        "device": device.device_id,
        "approval": approved,
        "result": approved,
        **advanced,
    }


@app.post("/v1/approvals/{approval_id}/reject")
async def v1_reject(approval_id: str, device: Device = Depends(_require_device)) -> dict:
    return {
        "device": device.device_id,
        "result": await _proxy("POST", f"/approvals/{approval_id}/reject", device),
    }


async def _advance_task(device: Device, task_id: str, max_steps: int = 16) -> dict:
    """Run steps until the task gates, finishes, or stops being runnable.

    Bounded by max_steps so a malformed plan can never loop forever. The
    loop never bypasses approval: a waiting_approval status ends it.
    """
    last: dict = {"task": None}
    for _ in range(max_steps):
        last = await _proxy("POST", f"/tasks/{task_id}/run", device)
        status = (last.get("task") or {}).get("status")
        if status not in ("running", "created"):
            break
        steps = (last.get("task") or {}).get("steps") or []
        if not any(s.get("status") == "created" for s in steps):
            break
    return last


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


@app.post("/v1/kill-phrase")
async def v1_kill_phrase(
    req: KillPhrasePayload, device: Device = Depends(_require_device)
) -> dict:
    """Kill-phrase emergency stop, independent of planning/LLM.

    The gateway matches the phrase itself so a device can force a stop even
    when the planner is unreachable or misbehaving. The task-runner still
    owns the hard stop and the audit record.
    """
    matched = detect_kill_phrase(req.transcript)
    killed = await _proxy(
        "POST", "/kill-phrase", device, {"transcript": req.transcript}
    )
    return {
        "device": device.device_id,
        "matched": matched,
        "killed": True,
        "stopped": killed.get("stopped", []),
        "count": killed.get("count", 0),
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


# ─── memory (Phase 4) ────────────────────────────────────────────────────────


async def _memory_request(method: str, path: str, body: dict | None = None) -> dict | list:
    url = f"{MEMORY_URL}{path}"
    try:
        resp = await _client().request(method, url, json=body, timeout=10.0)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"memory service unreachable: {exc}") from exc
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"detail": resp.text}
    if resp.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else str(payload)
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return payload


@app.get("/v1/memory")
async def v1_memory(device: Device = Depends(_require_device)) -> dict:
    return {"device": device.device_id, "memories": await _memory_request("GET", "/memory")}


@app.post("/v1/memory")
async def v1_memory_create(
    req: MemoryCreatePayload, device: Device = Depends(_require_device)
) -> dict:
    memory = await _memory_request("POST", "/memory", req.model_dump())
    return {"device": device.device_id, "memory": memory}


@app.post("/v1/memory/correct/{memory_id}")
async def v1_memory_correct(
    memory_id: str, req: MemoryCorrectPayload, device: Device = Depends(_require_device)
) -> dict:
    memory = await _memory_request(
        "POST", f"/memory/correct/{memory_id}", req.model_dump(exclude_none=True)
    )
    return {"device": device.device_id, "memory": memory}


@app.delete("/v1/memory/{memory_id}")
async def v1_memory_delete(memory_id: str, device: Device = Depends(_require_device)) -> dict:
    result = await _memory_request("DELETE", f"/memory/{memory_id}")
    return {"device": device.device_id, "result": result}


# ─── knowledge (Phase 4 / local vector index) ───────────────────────────────


async def _knowledge_request(method: str, path: str, body: dict | None = None) -> dict | list:
    """Proxy only to the local knowledge service; an ingest is still gated by
    that service's approval roots — the gateway never bypasses them."""
    url = f"{KNOWLEDGE_URL.rstrip('/')}{path}"
    try:
        resp = await _client().request(method, url, json=body, timeout=10.0)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"knowledge service unreachable: {exc}") from exc
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"detail": resp.text}
    if resp.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else str(payload)
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return payload


@app.get("/v1/knowledge/documents")
async def v1_knowledge_documents(device: Device = Depends(_require_device)) -> dict:
    return {
        "device": device.device_id,
        "documents": await _knowledge_request("GET", "/knowledge/documents"),
    }


@app.post("/v1/knowledge/ingest")
async def v1_knowledge_ingest(
    req: KnowledgeIngestPayload, device: Device = Depends(_require_device)
) -> dict:
    result = await _knowledge_request("POST", "/knowledge/ingest", req.model_dump())
    return {"device": device.device_id, "result": result}


@app.post("/v1/knowledge/query")
async def v1_knowledge_query(
    req: KnowledgeQueryPayload, device: Device = Depends(_require_device)
) -> dict:
    result = await _knowledge_request("POST", "/knowledge/query", req.model_dump())
    return {"device": device.device_id, "result": result}


# ─── connections: honest status of every integration ────────────────────────


_CONNECTION_CATALOG: list[tuple[str, str, str, str, list[str]]] = [
    ("gmail", "Gmail", "communication", "VIORUS_GMAIL_URL", ["Read unread mail", "Draft summaries"]),
    ("calendar", "Calendar", "communication", "VIORUS_CALENDAR_URL", ["Upcoming events"]),
    ("whatsapp", "WhatsApp", "communication", "VIORUS_WHATSAPP_URL", ["Read digest", "Send after approval"]),
    ("contacts", "Contacts", "knowledge", "VIORUS_CONTACTS_URL", ["Search people"]),
    ("notes", "Notes", "knowledge", "VIORUS_NOTES_URL", ["Recent notes"]),
    ("bookmarks", "Bookmarks", "knowledge", "VIORUS_BOOKMARKS_URL", ["Search saved links"]),
    ("history", "Browser history", "knowledge", "VIORUS_HISTORY_URL", ["Recent pages"]),
    ("cloud_files", "Cloud files", "knowledge", "VIORUS_CLOUD_FILES_URL", ["Recent documents"]),
    ("telephony", "Telephony", "actions", "VIORUS_TELEPHONY_URL", ["Prepare a call (never auto-dial)"]),
    ("reservations", "Reservations", "actions", "VIORUS_RESERVATIONS_URL", ["Search slots", "Book after approval"]),
    ("smart_home", "Smart home", "actions", "VIORUS_SMART_HOME_URL", ["Read status", "Control after approval"]),
    ("computer", "Computer agent", "actions", "VIORUS_COMPUTER_URL", ["Screen mirror", "Remote input after approval"]),
    ("browser", "Browser agent", "actions", "VIORUS_BROWSER_URL", ["Open URL", "Read page"]),
    ("documents", "Documents agent", "actions", "VIORUS_DOCUMENTS_URL", ["Search files", "Read allow-listed files"]),
    ("knowledge", "Knowledge index", "knowledge", "VIORUS_KNOWLEDGE_URL", ["Ingest approved docs", "Source-cited answers"]),
]


async def _probe(url: str, timeout: float = 1.2) -> tuple[bool, str]:
    try:
        resp = await _client().get(url, timeout=timeout)
        return True, f"reachable (HTTP {resp.status_code})"
    except httpx.RequestError:
        return False, "not reachable"
    except Exception:  # noqa: BLE001
        return False, "not reachable"


async def _unconfigured() -> tuple[bool, str]:
    """No URL in the environment: report honestly instead of probing."""
    return False, "not configured"


@app.get("/v1/connections")
async def v1_connections(device: Device = Depends(_require_device)) -> dict:
    """Report what is actually configured and reachable. Never invents a
    connected state: an unreachable connector is reported as not configured,
    and demo connectors are labelled as sample data."""
    demo_mode = os.getenv("VIORIS_DEMO_CONNECTORS", "0") == "1"

    # The computer agent is a real local daemon, not a sample connector: its
    # health is the same endpoint the task-runner uses, and it stays local
    # even while the sample connectors are switched on.
    health_path = {"computer": "/windows"}
    local_only = {"computer", "browser", "documents", "knowledge"}

    async def probe_entry(key: str, env: str) -> tuple[bool, str]:
        base = os.getenv(env)
        if not base:
            return await _unconfigured()
        return await _probe(base.rstrip("/") + health_path.get(key, "/health"))

    probes = await asyncio.gather(
        *(probe_entry(key, env) for key, _, _, env, _ in _CONNECTION_CATALOG)
    )
    items = []
    for (key, name, category, env, capabilities), (reachable, detail) in zip(
        _CONNECTION_CATALOG, probes, strict=True
    ):
        if not reachable:
            mode = "off"
        elif key in local_only:
            mode = "local"
        else:
            mode = "demo" if demo_mode else "live"
        items.append(
            {
                "id": key,
                "name": name,
                "category": category,
                "configured": reachable,
                "mode": mode,
                "detail": detail,
                "capabilities": capabilities,
                "note": (
                    "Sample data — no real account is connected."
                    if mode == "demo"
                    else ("" if reachable else f"Set {env} to enable.")
                ),
            }
        )

    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    (memory_ok, memory_detail), (ollama_ok, ollama_detail) = await asyncio.gather(
        _probe(MEMORY_URL.rstrip("/") + "/memory"),
        _probe(ollama_base + "/api/tags"),
    )
    items.append(
        {
            "id": "memory",
            "name": "Memory",
            "category": "local",
            "configured": memory_ok,
            "mode": "local" if memory_ok else "off",
            "detail": memory_detail,
            "capabilities": ["Preferences", "People", "Projects", "Routines", "Sensitive (opt-in)"],
            "note": "" if memory_ok else "Memory service is not running.",
        }
    )

    items.append(
        {
            "id": "ollama",
            "name": "Local planner (Ollama)",
            "category": "local",
            "configured": ollama_ok,
            "mode": "local" if ollama_ok else "off",
            "detail": ollama_detail,
            "capabilities": ["Freeform planning", "Local only · $0 per call"],
            "note": "" if ollama_ok else "Freeform planning falls back to deterministic workflows.",
        }
    )

    return {
        "device": device.device_id,
        "demo_mode": demo_mode,
        "connections": items,
        "configured_count": sum(1 for i in items if i["configured"]),
        "total": len(items),
    }


# ─── Phase 5 remote control (Prompt 5.3) ─────────────────────────────────────


async def _computer_request(method: str, path: str, body: dict | None = None) -> dict:
    """Talk to the computer agent daemon. It is localhost-only and session-gated."""
    url = f"{COMPUTER_URL}{path}"
    try:
        resp = await _client().request(method, url, json=body, timeout=10.0)
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
        resp = await _client().request(method, url, timeout=15.0)
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


@app.get("/v1/system/status")
async def v1_system_status(device: Device = Depends(_require_device)) -> dict:
    """Honest runtime state for Settings: what is actually live and locked."""
    demo_mode = os.getenv("VIORIS_DEMO_CONNECTORS", "0") == "1"
    critical_pin = bool(os.getenv("VIORIS_CRITICAL_PIN", ""))
    (runner_ok, runner_detail), (memory_ok, memory_detail), (computer_ok, computer_detail) = (
        await asyncio.gather(
            _probe(TASK_RUNNER_URL.rstrip("/") + "/tasks"),
            _probe(MEMORY_URL.rstrip("/") + "/memory"),
            _probe(COMPUTER_URL.rstrip("/") + "/windows"),
        )
    )
    return {
        "device": device.device_id,
        "registry_frozen": PermissionEngine._frozen,
        "tools_registered": len(PermissionEngine.list_tools()),
        "demo_mode": demo_mode,
        "critical_actions": "unlocked" if critical_pin else "locked",
        "critical_note": (
            "Critical approvals require the local PIN plus a 5s cooldown."
            if critical_pin
            else "Set VIORIS_CRITICAL_PIN to allow Critical approvals. They stay blocked until then."
        ),
        "local_only": True,
        "task_runner": TASK_RUNNER_URL,
        "memory_service": MEMORY_URL,
        "computer_agent": COMPUTER_URL,
        "task_runner_state": {"reachable": runner_ok, "detail": runner_detail},
        "memory_state": {"reachable": memory_ok, "detail": memory_detail},
        "computer_agent_state": {"reachable": computer_ok, "detail": computer_detail},
    }


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