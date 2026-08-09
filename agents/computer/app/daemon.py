"""
Computer agent daemon — FastAPI service (Phase 3, Prompt 3.1).

Exposes the four computer tools as HTTP endpoints, each returning a VERIFIED
ActionOutcome. Run from the repo root:

    uvicorn agents.computer.app.daemon:app --port 8430
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from packages.shared.permission_engine import (
    register_phase3_tools,
)

from .agent import ComputerAgent


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase3_tools()
    yield


app = FastAPI(title="Vioris Computer Agent", version="0.1.0", lifespan=lifespan)

_agent: ComputerAgent | None = None


def get_agent() -> ComputerAgent:
    global _agent
    if _agent is None:
        _agent = ComputerAgent()
    return _agent


class OpenAppRequest(BaseModel):
    name: str


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