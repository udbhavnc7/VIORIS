"""
Notes Connector Service (Phase 6).

FastAPI surface for the READ-ONLY notes/documents connector: OAuth connect,
recent-notes digest (with bodies), disconnect/revoke, account status. The
digest endpoint is Observe-tier and registers `notes.list_recent`, so the
permission engine decides whether a request may read — not the endpoint.
Tokens stay in the encrypted vault.

Run:
    uvicorn integrations.notes.api:app --port 8449
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

from ..base import ConnectionMissingError, ExpiredSessionError, RateLimitError
from ..token_vault import TokenVault
from .connector import HttpxNotesTransport, NotesConnector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe-tier reads only
    yield


app = FastAPI(title="Vioris Notes Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: NotesConnector | None = None
_pending: dict[str, str] = {}


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> NotesConnector:
    global _connector
    if _connector is None:
        client_id = os.getenv("VIORUS_DOCS_CLIENT_ID", "")
        client_secret = os.getenv("VIORUS_DOCS_CLIENT_SECRET", "")
        transport = HttpxNotesTransport(client_id, client_secret)
        _connector = NotesConnector(get_vault(), transport=transport)
    return _connector


class ConnectStartRequest(BaseModel):
    redirect_uri: str = "http://127.0.0.1:8449/oauth/authorized"


class DigestResponse(BaseModel):
    account: str
    window_hours: int
    total: int
    truncated: bool
    notes: list[dict]


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "notes-connector",
        "notes_list_recent_observed": PermissionEngine.is_registered("notes.list_recent"),
    }


@app.get("/accounts")
def list_accounts() -> list[dict]:
    accounts = get_vault().list("notes")
    for acct in accounts:
        acct["permission_explanation"] = get_connector().permission_explanation
    return accounts


@app.post("/connect/start")
def connect_start(req: ConnectStartRequest) -> dict:
    import secrets
    state = secrets.token_urlsafe(16)
    _pending[state] = req.redirect_uri
    url = get_connector().authorize_uri(state, req.redirect_uri)
    return {"state": state, "authorize_url": url}


@app.post("/connect/complete")
def connect_complete(state: str, code: str, redirect_uri: str | None = None) -> dict:
    expected = _pending.pop(state, None)
    if expected is None:
        raise HTTPException(status_code=400, detail="unknown or expired OAuth state")
    uri = redirect_uri or expected
    try:
        return get_connector().connect(code, uri)
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@app.get("/digest")
def recent_digest(
    hours: int = Query(default=168, ge=1, le=2160),
    max_results: int = Query(default=20, ge=1, le=50),
    account: str | None = None,
) -> DigestResponse:
    """Read-only recent-notes digest. Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data is
    fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("notes.list_recent")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"notes.list_recent is {result.tier.value}, refusing")

    conn = get_connector()
    accounts = get_vault().list("notes")
    if not accounts:
        raise HTTPException(status_code=409, detail="no notes account connected; call /connect/start first")
    identity = account or accounts[0]["account"]
    try:
        digest = conn.fetch_recent_digest(identity, hours=hours, max_results=max_results)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return DigestResponse(
        account=digest.account,
        window_hours=digest.window_hours,
        total=digest.total,
        truncated=digest.truncated,
        notes=[_note_dict(n) for n in digest.notes],
    )


@app.post("/disconnect/{account}")
def disconnect(account: str) -> dict:
    conn = get_connector()
    if not get_vault().list("notes"):
        raise HTTPException(status_code=404, detail="no notes account connected")
    try:
        conn.revoke_and_disconnect(account)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "notes", "account": account, "disconnected": True}


def _note_dict(n: Any) -> dict:
    return {
        "note_id": n.note_id,
        "title": n.title,
        "updated_at": n.updated_at.isoformat(),
        "summary": n.summary,
        "body": n.body,
    }