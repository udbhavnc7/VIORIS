"""
Bookmarks Connector Service (Phase 6).

FastAPI surface for the READ-ONLY local-bookmarks connector: point at a
browser profile snapshot, search the digest, forget the snapshot. The search
endpoint is Observe-tier and registers `bookmarks.search`, so the permission
engine decides whether a request may read — not the endpoint. The snapshot
path is stored encrypted in the vault.

Run:
    uvicorn integrations.bookmarks.api:app --port 8451
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from packages.shared.permission_engine import (
    PermissionEngine,
    UnknownToolError,
    register_phase6_connector_tools,
)

from ..base import ConnectionMissingError, ConnectorError
from ..token_vault import TokenVault
from .connector import BookmarksConnector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe-tier reads only
    yield


app = FastAPI(title="Vioris Bookmarks Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: BookmarksConnector | None = None


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> BookmarksConnector:
    global _connector
    if _connector is None:
        _connector = BookmarksConnector(get_vault())
    return _connector


class ConnectRequest(BaseModel):
    source: str = "bookmarks.json"  # path to the local Bookmarks JSON snapshot


class SearchResponse(BaseModel):
    source: str
    total: int
    bookmarks: list[dict]


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "bookmarks-connector",
        "bookmarks_search_observed": PermissionEngine.is_registered("bookmarks.search"),
    }


@app.get("/sources")
def list_sources() -> list[dict]:
    sources = get_vault().list("bookmarks")
    for s in sources:
        s["permission_explanation"] = get_connector().permission_explanation
    return sources


@app.post("/connect")
def connect(req: ConnectRequest) -> dict:
    """Point at a local bookmarks snapshot file (encrypted path in vault)."""
    try:
        return get_connector().connect_snapshot(req.source)
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/search")
def search(
    query: str = Query(default="", max_length=100),
    max_results: int = Query(default=50, ge=1, le=200),
    source: str | None = None,
) -> SearchResponse:
    """Read-only bookmark search/digest. Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data is
    fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("bookmarks.search")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"bookmarks.search is {result.tier.value}, refusing")

    conn = get_connector()
    sources = get_vault().list("bookmarks")
    if not sources:
        raise HTTPException(status_code=409, detail="no bookmark snapshot connected; call /connect first")
    ref = source or sources[0]["account"]
    try:
        digest = conn.search_bookmarks(ref, query=query, max_results=max_results)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SearchResponse(
        source=digest.source,
        total=digest.total,
        bookmarks=[_bookmark_dict(b) for b in digest.entries],
    )


@app.post("/disconnect/{source}")
def disconnect(source: str) -> dict:
    conn = get_connector()
    try:
        conn.disconnect_snapshot(source)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "bookmarks", "source": source, "disconnected": True}


def _bookmark_dict(b: Any) -> dict:
    return {
        "url": b.url,
        "title": b.title,
        "folder": b.folder,
        "added_at": b.added_at.isoformat() if b.added_at else None,
        "summary": b.summary,
    }