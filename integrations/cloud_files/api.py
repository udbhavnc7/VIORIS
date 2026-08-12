"""
Cloud Files Connector Service (Phase 6).

FastAPI surface for the READ-ONLY cloud-files connector: OAuth connect,
recent-files digest (metadata only), disconnect/revoke, account status. The
digest endpoint is Observe-tier and registers `cloud_files.list_recent`, so the
permission engine decides whether a request may read — not the endpoint.
Tokens stay in the encrypted vault.

Run:
    uvicorn integrations.cloud_files.api:app --port 8448
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
from .connector import DriveConnector, HttpxDriveTransport


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe-tier reads only
    yield


app = FastAPI(title="Vioris Cloud Files Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: DriveConnector | None = None
_pending: dict[str, str] = {}


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> DriveConnector:
    global _connector
    if _connector is None:
        client_id = os.getenv("VIORUS_DRIVE_CLIENT_ID", "")
        client_secret = os.getenv("VIORUS_DRIVE_CLIENT_SECRET", "")
        transport = HttpxDriveTransport(client_id, client_secret)
        _connector = DriveConnector(get_vault(), transport=transport)
    return _connector


class ConnectStartRequest(BaseModel):
    redirect_uri: str = "http://127.0.0.1:8448/oauth/authorized"


class DigestResponse(BaseModel):
    account: str
    window_hours: int
    total: int
    truncated: bool
    files: list[dict]


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "cloud-files-connector",
        "cloud_files_list_recent_observed": PermissionEngine.is_registered("cloud_files.list_recent"),
    }


@app.get("/accounts")
def list_accounts() -> list[dict]:
    accounts = get_vault().list("cloud_files")
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
    hours: int = Query(default=72, ge=1, le=720),
    max_results: int = Query(default=20, ge=1, le=50),
    account: str | None = None,
) -> DigestResponse:
    """Read-only recent-files digest (metadata only). Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data is
    fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("cloud_files.list_recent")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"cloud_files.list_recent is {result.tier.value}, refusing")

    conn = get_connector()
    accounts = get_vault().list("cloud_files")
    if not accounts:
        raise HTTPException(status_code=409, detail="no cloud-files account connected; call /connect/start first")
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
        files=[_file_dict(f) for f in digest.files],
    )


@app.post("/disconnect/{account}")
def disconnect(account: str) -> dict:
    conn = get_connector()
    if not get_vault().list("cloud_files"):
        raise HTTPException(status_code=404, detail="no cloud-files account connected")
    try:
        conn.revoke_and_disconnect(account)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "cloud_files", "account": account, "disconnected": True}


def _file_dict(f: Any) -> dict:
    return {
        "file_id": f.file_id,
        "name": f.name,
        "mime_type": f.mime_type,
        "owner": f.owner,
        "modified_at": f.modified_at.isoformat(),
        "is_folder": f.is_folder,
        "summary": f.summary,
    }