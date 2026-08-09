"""
Browser agent daemon — FastAPI service (Phase 3, Prompt 3.2).

Run from the repo root:

    uvicorn agents.browser.app.daemon:app --port 8431
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel

from packages.shared.permission_engine import register_phase3_browser_tools

from .agent import BrowserAgent


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase3_browser_tools()
    yield


app = FastAPI(title="Vioris Browser Agent", version="0.1.0", lifespan=lifespan)
_agent: BrowserAgent | None = None


def get_agent() -> BrowserAgent:
    global _agent
    if _agent is None:
        _agent = BrowserAgent()
    return _agent


class NavigateRequest(BaseModel):
    url: str


class FillRequest(BaseModel):
    description: str
    value: str


class ClickRequest(BaseModel):
    description: str


def _outcome(outcome) -> dict:
    return {
        "tool": outcome.tool,
        "ok": outcome.ok,
        "note": outcome.note,
        "detail": outcome.detail,
        "blocked": outcome.blocked,
        "error": outcome.error,
    }


@app.post("/navigate")
def navigate(req: NavigateRequest) -> dict:
    return _outcome(get_agent().navigate(req.url))


@app.get("/page")
def page() -> dict:
    return _outcome(get_agent().read_page())


@app.post("/fill")
def fill(req: FillRequest) -> dict:
    return _outcome(get_agent().fill_field(req.description, req.value))


@app.post("/click")
def click(req: ClickRequest) -> dict:
    return _outcome(get_agent().click_element(req.description))