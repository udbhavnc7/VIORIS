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

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from packages.shared.permission_engine import (
    PermissionEngine,
    UnknownToolError,
    register_phase6_connector_tools,
)

from ..base import (
    ConnectionMissingError,
    ConnectorError,
    ExpiredSessionError,
    PermissionNotApprovedError,
    RateLimitError,
)
from ..token_vault import TokenVault
from .connector import GmailConnector, HttpxGmailTransport
from .write_connector import SCOPE_MODIFY, SCOPE_SEND, GmailWriteConnector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe-tier reads only
    yield


app = FastAPI(title="Vioris Gmail Connector", version="0.1.0", lifespan=lifespan)


def _data_dir() -> Path:
    """Resolve the shared data dir, accepting the historical VIORUS_ spelling."""
    raw = os.getenv("VIORIS_DATA_DIR") or os.getenv("VIORUS_DATA_DIR", "vioris_data")
    return Path(raw)


def _credentials() -> tuple[str, str]:
    """Resolve Google OAuth credentials. VIORIS_ is canonical; VIORUS_ is
    accepted so older local setups keep working. As a chat-safe alternative,
    credentials can live in vioris_data/gmail_credentials.json, which is
    gitignored. Values never enter logs or responses."""
    client_id = os.getenv("VIORIS_GMAIL_CLIENT_ID") or os.getenv(
        "VIORUS_GMAIL_CLIENT_ID", ""
    )
    client_secret = os.getenv("VIORIS_GMAIL_CLIENT_SECRET") or os.getenv(
        "VIORUS_GMAIL_CLIENT_SECRET", ""
    )
    if client_id and client_secret:
        return client_id, client_secret
    cred_file = _data_dir() / "gmail_credentials.json"
    if cred_file.exists():
        try:
            data = json.loads(cred_file.read_text("utf-8"))
            client_id = client_id or str(data.get("client_id", ""))
            client_secret = client_secret or str(data.get("client_secret", ""))
        except (OSError, ValueError):
            # Corrupt or unreadable file fails closed: empty credentials.
            pass
    return client_id, client_secret


_vault: TokenVault | None = None
_connector: GmailConnector | None = None
_write_connector: GmailWriteConnector | None = None
#: OAuth state -> (redirect_uri, include_write_scope)
_pending: dict[str, tuple[str, bool]] = {}


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = _data_dir()
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> GmailConnector:
    global _connector
    if _connector is None:
        client_id, client_secret = _credentials()
        if not client_id or not client_secret:
            raise HTTPException(status_code=503, detail="Gmail OAuth credentials are not configured")
        transport = HttpxGmailTransport(client_id, client_secret)
        with_clone = GmailConnector(get_vault(), transport=transport)
        _connector = with_clone
    return _connector


def get_write_connector() -> GmailWriteConnector:
    """The separate write connector. It never reuses the read path."""
    global _write_connector
    if _write_connector is None:
        client_id, client_secret = _credentials()
        if not client_id or not client_secret:
            raise HTTPException(status_code=503, detail="Gmail OAuth credentials are not configured")
        transport = HttpxGmailTransport(client_id, client_secret)
        _write_connector = GmailWriteConnector(get_vault(), transport=transport)
    return _write_connector


def get_approval_store():
    """Open the task-runner's approval ledger (shared DB, same data dir)."""
    from services.task_runner.app.approvals import ApprovalStore

    return ApprovalStore(_data_dir() / "task_runner.db")


def _approval_verifier(
    task_id: str | None, step_id: str | None, idempotency_key: str
):
    def verify() -> bool:
        if not task_id or not step_id:
            return False
        return get_approval_store().is_approved_for_step(
            task_id, step_id, idempotency_key
        )

    return verify


class ConnectStartRequest(BaseModel):
    redirect_uri: str = "http://127.0.0.1:8445/oauth/authorized"
    #: Opt in to the write scopes (modify + send). Default stays read-only.
    include_write: bool = False


class DigestResponse(BaseModel):
    account: str
    window_hours: int
    total_unread: int
    truncated: bool
    items: list[dict]
    urgencies: dict[str, int]


class ReactRequest(BaseModel):
    message_id: str
    reaction: str = "acknowledge"
    label: str | None = None
    account: str | None = None
    idempotency_key: str | None = None
    task_id: str | None = None
    step_id: str | None = None


class ReactResponse(BaseModel):
    message_id: str
    reaction: str
    added_labels: list[str]
    removed_labels: list[str]
    idempotency_key: str
    replay: bool


class ReplyRequest(BaseModel):
    thread_id: str
    to: str
    subject: str
    body: str
    message_id: str | None = None
    account: str | None = None
    idempotency_key: str | None = None
    task_id: str | None = None
    step_id: str | None = None


class ReplyResponse(BaseModel):
    message_id: str
    thread_id: str
    to: str
    subject: str
    idempotency_key: str
    replay: bool


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "gmail-connector",
        "gmail_read_unread_observed": PermissionEngine.is_registered("gmail.read_unread"),
        "gmail_react_execute": PermissionEngine.is_registered("gmail.react"),
        "gmail_send_reply_execute": PermissionEngine.is_registered("gmail.send_reply"),
    }


@app.get("/accounts")
def list_accounts() -> list[dict]:
    """Connected accounts + their granted scope. Tokens never leave the vault."""
    accounts = get_vault().list("gmail")
    for acct in accounts:
        scopes = acct.get("scopes") or []
        if SCOPE_MODIFY in scopes or SCOPE_SEND in scopes:
            acct["permission_explanation"] = get_write_connector().permission_explanation
        else:
            acct["permission_explanation"] = get_connector().permission_explanation
    return accounts


@app.post("/connect/start")
def connect_start(req: ConnectStartRequest) -> dict:
    """Start OAuth: returns the URL the user visits in a browser (device flow
    not assumed — this is the laptop's own browser)."""
    import secrets
    state = secrets.token_urlsafe(16)
    _pending[state] = (req.redirect_uri, req.include_write)
    connector = get_write_connector() if req.include_write else get_connector()
    url = connector.authorize_uri(state, req.redirect_uri)
    return {
        "state": state,
        "authorize_url": url,
        "scopes": "read+write" if req.include_write else "read",
    }


@app.post("/connect/complete")
def connect_complete(state: str, code: str, redirect_uri: str | None = None) -> dict:
    """Swap the authorization code for an encrypted vault entry (read-only)."""
    expected = _pending.pop(state, None)
    if expected is None:
        raise HTTPException(status_code=400, detail="unknown or expired OAuth state")
    expected_uri, include_write = expected
    uri = redirect_uri or expected_uri
    connector = get_write_connector() if include_write else get_connector()
    try:
        summary = connector.connect(code, uri)
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return summary


@app.get("/oauth/authorized", response_class=HTMLResponse)
def oauth_authorized(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Google redirects here after consent.

    The authorization code is a single-use credential for the local vault, so
    this page finishes the exchange immediately and shows only a safe summary —
    never the code, token, or secret. Anything unexpected is reported honestly.
    """
    if error:
        detail = escape(error)
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>Vioris — Gmail</title>"
            "<body><h2>Gmail was not connected</h2>"
            f"<p>Google reported: {detail}</p>"
            "<p>You can close this tab and try again.</p></body>"
        )
    if not code or not state:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>Vioris — Gmail</title>"
            "<body><h2>Gmail was not connected</h2>"
            "<p>Missing code or state in the OAuth callback.</p></body>",
            status_code=400,
        )
    try:
        summary = connect_complete(state=state, code=code)
    except HTTPException as exc:
        detail = escape(str(exc.detail))
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>Vioris — Gmail</title>"
            "<body><h2>Gmail was not connected</h2>"
            f"<p>{detail}</p></body>",
            status_code=exc.status_code,
        )
    account = escape(str(summary.get("account", "Gmail")))
    scopes = escape(", ".join(summary.get("scopes", [])))
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Vioris — Gmail connected</title>"
        "<body><h2>Gmail connected</h2>"
        f"<p>Account: {account}</p><p>Scopes: {scopes}</p>"
        "<p>Tokens are stored only in Vioris's local encrypted vault. "
        "You can close this tab.</p></body>"
    )


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


def _require_execute(tool: str) -> None:
    """Refuse unless the static registry says this tool is EXECUTE.

    The endpoint never decides risk; it only checks the registry. A missing or
    lower-tier registration blocks before any content is touched.
    """
    try:
        result = PermissionEngine.classify(tool)
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "execute":
        raise HTTPException(
            status_code=403, detail=f"{tool} is {result.tier.value}, refusing"
        )


def _connected_identity(account: str | None) -> str:
    accounts = get_vault().list("gmail")
    if not accounts:
        raise HTTPException(
            status_code=409,
            detail="no Gmail account connected; call /connect/start first",
        )
    return account or accounts[0]["account"]


@app.post("/react", response_model=ReactResponse)
def react(req: ReactRequest) -> ReactResponse:
    """Apply one reversible reaction to one email. Execute-tier.

    Permission gate: `gmail.react` must be registered at EXECUTE, and the
    connector re-verifies an approved task-runner ledger record for this
    task+step+idempotency_key. No approval means nothing is touched.
    """
    if not req.idempotency_key:
        raise HTTPException(status_code=422, detail="idempotency_key is required for a reaction")
    _require_execute("gmail.react")
    identity = _connected_identity(req.account)
    try:
        result = get_write_connector().react(
            identity,
            message_id=req.message_id,
            reaction=req.reaction,
            label=req.label,
            idempotency_key=req.idempotency_key,
            approval_verifier=_approval_verifier(
                req.task_id, req.step_id, req.idempotency_key
            ),
        )
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except PermissionNotApprovedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReactResponse(
        message_id=result.message_id,
        reaction=result.reaction,
        added_labels=result.added_labels,
        removed_labels=result.removed_labels,
        idempotency_key=result.idempotency_key,
        replay=result.replay,
    )


@app.post("/reply", response_model=ReplyResponse)
def reply(req: ReplyRequest) -> ReplyResponse:
    """Send an approved reply in an existing thread. Execute-tier.

    Same gates as `/react`: static registry says EXECUTE, and the connector
    verifies the approved ledger record. A replayed idempotency_key returns the
    prior message instead of sending a duplicate.
    """
    if not req.idempotency_key:
        raise HTTPException(status_code=422, detail="idempotency_key is required for a reply")
    _require_execute("gmail.send_reply")
    identity = _connected_identity(req.account)
    try:
        result = get_write_connector().send_reply(
            identity,
            thread_id=req.thread_id,
            to=req.to,
            subject=req.subject,
            body=req.body,
            idempotency_key=req.idempotency_key,
            approval_verifier=_approval_verifier(
                req.task_id, req.step_id, req.idempotency_key
            ),
        )
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except PermissionNotApprovedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReplyResponse(
        message_id=result.message_id,
        thread_id=result.thread_id,
        to=result.to,
        subject=result.subject,
        idempotency_key=result.idempotency_key,
        replay=result.replay,
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
    from packages.shared.untrusted_content import UntrustedContent

    # Snippets/asks come from an external sender: tag them as observed data so
    # a digest composer cannot mistake injected text for a user instruction.
    observed = UntrustedContent(
        source="email",
        location=getattr(item, "sender", ""),
        text=" ".join(
            str(getattr(item, field, "") or "")
            for field in ("subject", "snippet")
        ),
    )
    return {
        "message_id": item.message_id,
        "sender": item.sender,
        "subject": item.subject,
        "urgency": item.urgency,
        "snippet": item.snippet,
        "asks": item.asks,
        "dates": item.dates,
        "received_at": item.received_at.isoformat() if item.received_at else None,
        "untrusted": observed.to_dict(),
    }