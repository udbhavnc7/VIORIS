"""
Gmail Connector Service (Phase 6, Prompt 6.1).

FastAPI surface for the READ-ONLY Gmail connector: OAuth connect, unread
digest, disconnect/revoke. The digest endpoint is Observe-tier and registers
`gmail.read_unread` so the permission engine is the gate that decides whether a
request is allowed. Tokens stay in the encrypted vault; the API never returns
or logs them.

Run:
    uvicorn integrations.gmail.api:app --port 8445
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
from .connector import GmailConnector, HttpxGmailTransport


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe-tier reads only
    yield


app = FastAPI(title="Vioris Gmail Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: GmailConnector | None = None
_pending: dict[str, str] = {}


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> GmailConnector:
    global _connector
    if _connector is None:
        client_id = os.getenv("VIORUS_GMAIL_CLIENT_ID", "")
        client_secret = os.getenv("VIORUS_GMAIL_CLIENT_SECRET", "")
        transport = HttpxGmailTransport(client_id, client_secret)
        with_clone = GmailConnector(get_vault(), transport=transport)
        _connector = with_clone
    return _connector


class ConnectStartRequest(BaseModel):
    redirect_uri: str = "http://127.0.0.1:8445/oauth/authorized"


class DigestResponse(BaseModel):
    account: str
    window_hours: int
    total_unread: int
    truncated: bool
    items: list[dict]
    urgencies: dict[str, int]


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "gmail-connector",
        "gmail_read_unread_observed": PermissionEngine.is_registered("gmail.read_unread"),
    }


@app.get("/accounts")
def list_accounts() -> list[dict]:
    """Connected accounts + their read scope. Tokens never leave the vault."""
    accounts = get_vault().list("gmail")
    for acct in accounts:
        acct["permission_explanation"] = get_connector().permission_explanation
    return accounts


@app.post("/connect/start")
def connect_start(req: ConnectStartRequest) -> dict:
    """Start OAuth: returns the URL the user visits in a browser (device flow
    not assumed — this is the laptop's own browser)."""
    import secrets
    state = secrets.token_urlsafe(16)
    _pending[state] = req.redirect_uri
    url = get_connector().authorize_uri(state, req.redirect_uri)
    return {"state": state, "authorize_url": url}


@app.post("/connect/complete")
def connect_complete(state: str, code: str, redirect_uri: str | None = None) -> dict:
    """Swap the authorization code for an encrypted vault entry (read-only)."""
    expected = _pending.pop(state, None)
    if expected is None:
        raise HTTPException(status_code=400, detail="unknown or expired OAuth state")
    uri = redirect_uri or expected
    try:
        summary = get_connector().connect(code, uri)
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return summary


@app.get("/digest")
def unread_digest(
    hours: int = Query(default=24, ge=1, le=168),
    max_results: int = Query(default=20, ge=1, le=50),
    account: str | None = None,
) -> DigestResponse:
    """Read-only 24h unread digest. Observe-tier: no approval, changes nothing.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint would refuse before any
    data was fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("gmail.read_unread")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":  # symmetry: read stays read
        raise HTTPException(status_code=403, detail=f"gmail.read_unread is {result.tier.value}, refusing")

    conn = get_connector()
    accounts = get_vault().list("gmail")
    if not accounts:
        raise HTTPException(status_code=409, detail="no Gmail account connected; call /connect/start first")
    identity = account or accounts[0]["account"]
    try:
        digest = conn.fetch_unread_digest(identity, hours=hours, max_results=max_results)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    urgencies = {"low": 0, "medium": 0, "high": 0}
    for item in digest.items:
        urgencies[item.urgency] += 1
    return DigestResponse(
        account=digest.account,
        window_hours=digest.window_hours,
        total_unread=digest.total_unread,
        truncated=digest.truncated,
        items=[_item_dict(i) for i in digest.items],
        urgencies=urgencies,
    )


@app.post("/disconnect/{account}")
def disconnect(account: str) -> dict:
    """Revoke on provider + delete the encrypted vault entry (audited)."""
    conn = get_connector()
    auto = get_vault().list("gmail")
    if not auto:
        raise HTTPException(status_code=404, detail="no Gmail account connected")
    try:
        conn.revoke_and_disconnect(account)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "gmail", "account": account, "disconnected": True}


def _item_dict(item: Any) -> dict:
    return {
        "message_id": item.message_id,
        "sender": item.sender,
        "subject": item.subject,
        "urgency": item.urgency,
        "snippet": item.snippet,
        "asks": item.asks,
        "dates": item.dates,
        "received_at": item.received_at.isoformat() if item.received_at else None,
    }