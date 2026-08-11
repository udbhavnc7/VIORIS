"""
Computer agent daemon — FastAPI service (Phase 3, Prompt 3.1 → Phase 5, Prompt 5.3).

Exposes the computer tools as HTTP endpoints, each returning a VERIFIED
ActionOutcome, plus Phase 5 remote control: start an auto-expiring session
for a paired device, and send keyboard/mouse input gated by that session.
Run from the repo root:

    uvicorn agents.computer.app.daemon:app --port 8430
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from packages.shared.permission_engine import (
    register_phase3_tools,
    register_phase5_remote_tools,
)

from .agent import ComputerAgent
from .remote_control import InputBackendError, RemoteControl, RemoteSessionError


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase3_tools()
    register_phase5_remote_tools()
    yield


app = FastAPI(title="Vioris Computer Agent", version="0.2.0", lifespan=lifespan)

_agent: ComputerAgent | None = None
_remote: RemoteControl | None = None


def get_agent() -> ComputerAgent:
    global _agent
    if _agent is None:
        _agent = ComputerAgent()
    return _agent


def get_remote() -> RemoteControl:
    global _remote
    if _remote is None:
        _remote = RemoteControl()
    return _remote


class OpenAppRequest(BaseModel):
    name: str


class StartSessionRequest(BaseModel):
    device_id: str
    timeout_minutes: int = Field(default=5, ge=1, le=60)


class EndSessionRequest(BaseModel):
    device_id: str


class InputRequest(BaseModel):
    session_id: str
    device_id: str
    action: str = Field(description="type | left | right | move")
    text: str | None = None
    x: int | None = None
    y: int | None = None


def _outcome(outcome) -> dict:
    return {
        "tool": outcome.tool,
        "ok": outcome.ok,
        "note": outcome.note,
        "detail": outcome.detail,
        "verified": outcome.verified,
        "verified_note": outcome.verified_note,
        "error": outcome.error,
    }


@app.get("/windows")
def windows() -> dict:
    return _outcome(get_agent().list_windows())


@app.get("/screenshot")
def screenshot() -> dict:
    return _outcome(get_agent().capture_screenshot())


@app.get("/screen-text")
def screen_text() -> dict:
    return _outcome(get_agent().read_screen_text())


@app.post("/open-app")
def open_app(req: OpenAppRequest) -> dict:
    if not req.name:
        raise HTTPException(status_code=422, detail="name is required")
    return _outcome(get_agent().open_app(req.name))


# ── Phase 5 remote control (Prompt 5.3) ──────────────────────────────────────


@app.post("/remote/session")
def remote_session(req: StartSessionRequest) -> dict:
    """Start an auto-expiring remote session for a paired device.

    Called ONLY after the task-runner has approved the execute-tier
    `computer.start_remote_session` step. Sessions expire at timeout_minutes.
    """
    session = get_remote().sessions.create(req.device_id, req.timeout_minutes)
    return {"session": session.to_dict()}


@app.get("/remote/session/active")
def active_session(device_id: str) -> dict:
    session = get_remote().sessions.active_for_device(device_id)
    if session is None:
        return {"session": None}
    return {"session": session.to_dict()}


@app.get("/remote/screenshot-image")
def screenshot_image(device_id: str) -> Response:
    """Actual PNG frame the phone renders, ONLY while a live session for this
    device exists (throttled on demand by the gateway, never a stream)."""
    if get_remote().sessions.active_for_device(device_id) is None:
        raise HTTPException(status_code=403, detail="no active remote session for this device")
    data = get_agent().capture_screenshot_bytes()
    if data is None:
        raise HTTPException(status_code=422, detail="screen capture produced no image")
    return Response(content=data, media_type="image/png")


@app.post("/remote/session/end")
def end_session(req: EndSessionRequest) -> dict:
    """Immediately kill every live session a device holds. Used by 'Stop
    Everything' and by the phone's explicit end control. No further input is
    accepted from that device until a fresh approved session starts."""
    ended = get_remote().sessions.end_all_for_device(req.device_id)
    return {"device_id": req.device_id, "ended_sessions": ended}


@app.post("/remote/lock")
def lock_workstation(req: EndSessionRequest) -> dict:
    """Remote 'lock now': EXECUTE-tier action gated by a live session for this
    device. Locks the laptop immediately and ends the session, so no further
    remote input flows until the user drains in person."""
    if get_remote().sessions.active_for_device(req.device_id) is None:
        raise HTTPException(status_code=403, detail="no active remote session for this device")
    reason = _outcome(get_agent().lock_workstation())
    ended = get_remote().sessions.end_all_for_device(req.device_id)
    return {**reason, "ended_sessions": ended}


@app.post("/remote/input")
def remote_input(req: InputRequest) -> dict:
    """Send keyboard/mouse input, but only while a NON-EXPIRED session for the
    exact device is live. A revoked/expired/unknown session means 403 — the
    input is never attempted."""
    try:
        get_remote().send_input(
            req.session_id,
            req.device_id,
            req.action,
            text=req.text,
            x=req.x,
            y=req.y,
        )
    except RemoteSessionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InputBackendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"delivered": True, "session_id": req.session_id, "action": req.action}