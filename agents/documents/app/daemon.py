"""
Documents agent daemon — FastAPI service (Phase 3, Prompt 3.3).

Run from the repo root (allow-lists come from env, see allowlist.py):

    VIORUS_ALLOWED_DIRS="C:\\Users\\me\\notes" \
    VIORUS_SHELL_PROGRAMS="git,node" \
    uvicorn agents.documents.app.daemon:app --port 8432
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel

from packages.shared.permission_engine import register_phase3_files_tools

from .agent import DocumentsAgent


async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase3_files_tools()
    yield


app = FastAPI(title="Vioris Documents Agent", version="0.1.0", lifespan=lifespan)
_agent: DocumentsAgent | None = None


def get_agent() -> DocumentsAgent:
    global _agent
    if _agent is None:
        _agent = DocumentsAgent()
    return _agent


class SearchRequest(BaseModel):
    query: str
    root: str | None = None


class PathRequest(BaseModel):
    path: str


class MoveRequest(BaseModel):
    source: str
    destination: str


class RenameRequest(BaseModel):
    path: str
    new_name: str


class TerminalRequest(BaseModel):
    command: str
    args: list[str] = []


def _outcome(outcome) -> dict:
    return {
        "tool": outcome.tool,
        "ok": outcome.ok,
        "note": outcome.note,
        "detail": outcome.detail,
        "blocked": outcome.blocked,
        "error": outcome.error,
    }


@app.post("/search")
def search(req: SearchRequest) -> dict:
    return _outcome(get_agent().search_files(req.query, req.root))


@app.post("/read")
def read_file(req: PathRequest) -> dict:
    return _outcome(get_agent().read_file(req.path))


@app.post("/move")
def move(req: MoveRequest) -> dict:
    return _outcome(get_agent().move(req.source, req.destination))


@app.post("/rename")
def rename(req: RenameRequest) -> dict:
    return _outcome(get_agent().rename(req.path, req.new_name))


@app.post("/delete")
def delete(req: PathRequest) -> dict:
    return _outcome(get_agent().delete(req.path))


@app.post("/terminal")
def terminal(req: TerminalRequest) -> dict:
    return _outcome(get_agent().run(req.command, req.args))