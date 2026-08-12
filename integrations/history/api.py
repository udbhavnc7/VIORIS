"""
History Connector Service (Phase 6).

FastAPI surface for the READ-ONLY local-browser-history connector: point at a
browser profile history file, read recent visits, forget the file. The read
endpoint is Observe-tier and registers `history.recent`, so the permission
engine decides whether a request may read — not the endpoint. The file path is
stored encrypted in the vault.

Run:
    uvicorn integrations.history.api:app --port 8452
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from packages.shared.permission_engine import (
    PermissionEngine,
    UnknownToolError,
    register_phase6_connector_tools,
)

from ..base import ConnectionMissingError, ConnectorError
from ..token_vault import TokenVault
from .connector import HistoryConnector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe-tier reads only
    yield


app = FastAPI(title="Vioris History Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: HistoryConnector | None = None


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> HistoryConnector:
    global _connector
    if _connector is None:
        _connector = HistoryConnector(get_vault())
    return _connector


class ConnectRequest(BaseModel):
    source: str = "History"  # path to the local Chromium History SQLite file


class RecentResponse(BaseModel):
    source: str
    total: int
    entries: list[dict]


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "history-connector",
        "history_recent_observed": PermissionEngine.is_registered("history.recent"),
    }


@app.get("/sources")
def list_sources() -> list[dict]:
    sources = get_vault().list("history")
    for s in sources:
        s["permission_explanation"] = get_connector().permission_explanation
    return sources


@app.post("/connect")
def connect(req: ConnectRequest) -> dict:
    """Point at a local history file (encrypted path in vault)."""
    try:
        return get_connector().connect_snapshot(req.source)
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/recent")
def recent(
    hours: int = Query(default=72, ge=1, le=24 * 30),
    max_results: int = Query(default=50, ge=1, le=200),
    source: str | None = None,
) -> RecentResponse:
    """Read-only recent-history digest. Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data is
    fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("history.recent")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"history.recent is {result.tier.value}, refusing")

    conn = get_connector()
    sources = get_vault().list("history")
    if not sources:
        raise HTTPException(status_code=409, detail="no history file connected; call /connect first")
    ref = source or sources[0]["account"]
    try:
        digest = conn.recent_history(ref, hours=hours, max_results=max_results)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return RecentResponse(
        source=digest.source,
        total=digest.total,
        entries=[_entry_dict(e) for e in digest.entries],
    )


@app.post("/disconnect/{source}")
def disconnect(source: str) -> dict:
    conn = get_connector()
    try:
        conn.disconnect_snapshot(source)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "history", "source": source, "disconnected": True}


def _entry_dict(e: Any) -> dict:
    return {
        "url": e.url,
        "title": e.title,
        "visited_at": e.visited_at.isoformat() if e.visited_at else None,
        "summary": e.summary,
    }
