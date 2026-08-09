"""
Vioris web console (Phase 1, local only).

Small FastAPI app bound to 127.0.0.1 so you can try the agent in a browser
without touching audio. It runs the exact same pipeline as the voice loop:
command_runner.run_command -> parse -> permission gate -> executor -> store.

Nothing here listens on a public interface and no account API is touched.
Explicitly NOT exposed over Tailscale until Phase 5 hardens it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from packages.shared.permission_engine import PermissionEngine
from packages.shared.audit import GENESIS_HASH

from .command_runner import run_command
from .config import AgentConfig
from .executor import Executor
from .store import TaskStore

logger = logging.getLogger(__name__)

app = FastAPI(title="Vioris Phase 1 Console")


class CommandRequest(BaseModel):
    transcript: str


def _live_store() -> TaskStore:
    return TaskStore(AgentConfig().db_path)


def _executor() -> Executor:
    return Executor()


@app.get("/", response_class=HTMLResponse)
def console() -> str:
    html = _CONSOLE_HTML
    return html


@app.post("/api/command")
def post_command(req: CommandRequest) -> dict:
    out = run_command(req.transcript, _executor(), _live_store())
    return out


@app.get("/api/tools")
def tools() -> list[dict]:
    return [
        {
            "tool_name": reg.tool_name,
            "tier": reg.tier.value,
            "confirmation_required": reg.confirmation_required,
            "description": reg.description,
        }
        for reg in sorted(PermissionEngine.list_tools(), key=lambda r: r.tool_name)
    ]


@app.get("/api/tasks")
def tasks() -> list[dict]:
    return _live_store().list_tasks(limit=50)


@app.get("/api/audit")
def audit() -> dict:
    store = _live_store()
    ok, problems = store.verify_chain()
    return {
        "genesis": GENESIS_HASH,
        "ok": ok,
        "problems": problems,
        "events": store.audit_events_copy(limit=100),
    }


_CONSOLE_HTML = Path(__file__).with_name("console.html").read_text(encoding="utf-8")
