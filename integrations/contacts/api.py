"""
Contacts Connector Service (Phase 6).

FastAPI surface for the READ-ONLY contacts connector: OAuth connect, contact
search/digest, disconnect/revoke, account status. The search endpoint is
Observe-tier and registers `contacts.search`, so the permission engine decides
whether a request may read — not the endpoint. Tokens stay in the encrypted
vault.

Run:
    uvicorn integrations.contacts.api:app --port 8450
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
from .connector import ContactsConnector, HttpxContactsTransport


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe-tier reads only
    yield


app = FastAPI(title="Vioris Contacts Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: ContactsConnector | None = None
_pending: dict[str, str] = {}


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> ContactsConnector:
    global _connector
    if _connector is None:
        client_id = os.getenv("VIORUS_CONTACTS_CLIENT_ID", "")
        client_secret = os.getenv("VIORUS_CONTACTS_CLIENT_SECRET", "")
        transport = HttpxContactsTransport(client_id, client_secret)
        _connector = ContactsConnector(get_vault(), transport=transport)
    return _connector


class ConnectStartRequest(BaseModel):
    redirect_uri: str = "http://127.0.0.1:8450/oauth/authorized"


class SearchResponse(BaseModel):
    account: str
    total: int
    truncated: bool
    contacts: list[dict]


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "contacts-connector",
        "contacts_search_observed": PermissionEngine.is_registered("contacts.search"),
    }


@app.get("/accounts")
def list_accounts() -> list[dict]:
    accounts = get_vault().list("contacts")
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


@app.get("/search")
def search(
    query: str = Query(default="", max_length=100),
    max_results: int = Query(default=30, ge=1, le=100),
    account: str | None = None,
) -> SearchResponse:
    """Read-only contact search/digest. Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data is
    fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("contacts.search")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"contacts.search is {result.tier.value}, refusing")

    conn = get_connector()
    accounts = get_vault().list("contacts")
    if not accounts:
        raise HTTPException(status_code=409, detail="no contacts account connected; call /connect/start first")
    identity = account or accounts[0]["account"]
    try:
        digest = conn.search_contacts(identity, query=query, max_results=max_results)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return SearchResponse(
        account=digest.account,
        total=digest.total,
        truncated=digest.truncated,
        contacts=[_contact_dict(c) for c in digest.contacts],
    )


@app.post("/disconnect/{account}")
def disconnect(account: str) -> dict:
    conn = get_connector()
    if not get_vault().list("contacts"):
        raise HTTPException(status_code=404, detail="no contacts account connected")
    try:
        conn.revoke_and_disconnect(account)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "contacts", "account": account, "disconnected": True}


def _contact_dict(c: Any) -> dict:
    return {
        "person_id": c.person_id,
        "name": c.name,
        "emails": c.emails,
        "phones": c.phones,
        "summary": c.summary,
    }