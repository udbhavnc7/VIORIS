"""
Calendar Connector Service (Phase 6).

FastAPI surface for the READ-ONLY calendar connector: OAuth connect, upcoming
digest, disconnect/revoke, account status. The digest endpoint is
Observe-tier and registers `calendar.read_upcoming`, so the permission engine
decides whether a request may read — not the endpoint. Tokens stay in the
encrypted vault.

Run:
    uvicorn integrations.calendar.api:app --port 8447
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

from ..base import ConnectionMissingError, ExpiredSessionError, RateLimitError
from ..token_vault import TokenVault
from .connector import CalendarConnector, HttpxCalendarTransport


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe-tier reads only
    yield


app = FastAPI(title="Vioris Calendar Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: CalendarConnector | None = None
_pending: dict[str, str] = {}


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> CalendarConnector:
    global _connector
    if _connector is None:
        client_id = os.getenv("VIORUS_CALENDAR_CLIENT_ID", "")
        client_secret = os.getenv("VIORUS_CALENDAR_CLIENT_SECRET", "")
        transport = HttpxCalendarTransport(client_id, client_secret)
        _connector = CalendarConnector(get_vault(), transport=transport)
    return _connector


class ConnectStartRequest(BaseModel):
    redirect_uri: str = "http://127.0.0.1:8447/oauth/authorized"


class DigestResponse(BaseModel):
    account: str
    window_hours: int
    total: int
    truncated: bool
    events: list[dict]


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "calendar-connector",
        "calendar_read_upcoming_observed": PermissionEngine.is_registered("calendar.read_upcoming"),
    }


@app.get("/accounts")
def list_accounts() -> list[dict]:
    accounts = get_vault().list("calendar")
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
def upcoming_digest(
    hours: int = Query(default=72, ge=1, le=720),
    max_results: int = Query(default=10, ge=1, le=50),
    account: str | None = None,
) -> DigestResponse:
    """Read-only upcoming-events digest. Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data is
    fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("calendar.read_upcoming")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"calendar.read_upcoming is {result.tier.value}, refusing")

    conn = get_connector()
    accounts = get_vault().list("calendar")
    if not accounts:
        raise HTTPException(status_code=409, detail="no calendar account connected; call /connect/start first")
    identity = account or accounts[0]["account"]
    try:
        digest = conn.fetch_upcoming_digest(identity, hours=hours, max_results=max_results)
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
        events=[_event_dict(e) for e in digest.events],
    )


@app.post("/disconnect/{account}")
def disconnect(account: str) -> dict:
    conn = get_connector()
    if not get_vault().list("calendar"):
        raise HTTPException(status_code=404, detail="no calendar account connected")
    try:
        conn.revoke_and_disconnect(account)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "calendar", "account": account, "disconnected": True}


def _event_dict(e: Any) -> dict:
    return {
        "event_id": e.event_id,
        "title": e.title,
        "start_at": e.start_at.isoformat(),
        "attendees": e.attendees,
        "urgency": e.urgency,
        "summary": e.summary,
        "location": e.location,
    }