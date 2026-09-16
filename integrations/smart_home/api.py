"""
Smart-home Connector Service (Phase 6).

FastAPI surface for the local smart-home hub connector: pair the hub, read
device/camera/sensor/energy status (Observe), and control non-critical devices
(Execute, gated by an approved ledger record). `smart_home.control` refuses
safety-critical categories (locks, alarms, security, doors, gates) at the
connector — an approval can never override that. Nothing controls a device
without the permission engine.

Run:
    uvicorn integrations.smart_home.api:app --port 8455
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
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
from .connector import SmartHomeConnector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    register_phase6_connector_tools()  # idempotent; Observe + Execute tiers
    yield


app = FastAPI(title="Vioris Smart-home Connector", version="0.1.0", lifespan=lifespan)

_vault: TokenVault | None = None
_connector: SmartHomeConnector | None = None


def get_vault() -> TokenVault:
    global _vault
    if _vault is None:
        base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
        _vault = TokenVault(base / "tokens.db", key_file=base / "token.key")
    return _vault


def get_connector() -> SmartHomeConnector:
    global _connector
    if _connector is None:
        _connector = SmartHomeConnector(get_vault())
    return _connector


def get_approval_store():
    """Open the task-runner's approval ledger (shared DB, same data dir)."""
    from services.task_runner.app.approvals import ApprovalStore

    base = Path(os.getenv("VIORUS_DATA_DIR", "vioris_data"))
    return ApprovalStore(base / "task_runner.db")


def _approval_verifier(task_id: str | None, step_id: str | None, idempotency_key: str) -> Callable[[], bool]:
    def verify() -> bool:
        if not task_id or not step_id:
            return False
        return get_approval_store().is_approved_for_step(task_id, step_id, idempotency_key)

    return verify


class ControlRequest(BaseModel):
    device_id: str
    action: str
    value: str
    idempotency_key: str
    hub_ref: str | None = None
    task_id: str | None = None
    step_id: str | None = None


class StatusResponse(BaseModel):
    total: int
    devices: list[dict]


class ControlResponse(BaseModel):
    device_id: str
    action: str
    value: str
    result: str
    idempotency_key: str
    replay: bool


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "smart-home-connector",
        "smart_home_device_status_observed": PermissionEngine.is_registered("smart_home.device_status"),
        "smart_home_control_execute": PermissionEngine.is_registered("smart_home.control"),
    }


@app.get("/hubs")
def list_hubs() -> list[dict]:
    hubs = get_vault().list("smart_home")
    for h in hubs:
        h["permission_explanation"] = get_connector().permission_explanation
    return hubs


@app.post("/link/start")
def link_start() -> dict:
    return get_connector().link_hub()


@app.post("/link/complete")
def link_complete(hub_ref: str) -> dict:
    try:
        return get_connector().complete_link(hub_ref)
    except (ConnectorError, ExpiredSessionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/status")
def status(
    category: str | None = None,
    hub_ref: str | None = None,
) -> StatusResponse:
    """Read-only device/sensor/energy status. Observe-tier: no approval.

    Permission gate: the tool must be registered at Observe. If it were
    unregistered / Execute / Critical, this endpoint refuses before any data is
    fetched — the engine decides, never the request.
    """
    try:
        result = PermissionEngine.classify("smart_home.device_status")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "observe":
        raise HTTPException(status_code=403, detail=f"smart_home.device_status is {result.tier.value}, refusing")

    conn = get_connector()
    hubs = get_vault().list("smart_home")
    if not hubs:
        raise HTTPException(status_code=409, detail="no smart-home hub paired; call /link/start first")
    ref = hub_ref or hubs[0]["account"]
    try:
        status_obj = conn.device_status(ref, category=category)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExpiredSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return StatusResponse(
        total=status_obj.total,
        devices=[_status_dict(d) for d in status_obj.devices],
    )


@app.post("/control")
def control(req: ControlRequest) -> ControlResponse:
    """Control a non-critical device. Execute-tier: only fires with an approved
    ledger entry for the caller's task+step+idempotency_key.

    Permission gate: `smart_home.control` must be registered at EXECUTE. The
    connector ALSO verifies the approval ledger, AND refuses safety-critical
    categories (locks, alarms, security, doors, gates) — no approval can
    override that.
    """
    if not req.idempotency_key:
        raise HTTPException(status_code=422, detail="idempotency_key is required for a control")
    try:
        result = PermissionEngine.classify("smart_home.control")
    except UnknownToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.tier.value != "execute":
        raise HTTPException(status_code=403, detail=f"smart_home.control is {result.tier.value}, refusing")

    conn = get_connector()
    hubs = get_vault().list("smart_home")
    if not hubs:
        raise HTTPException(status_code=409, detail="no smart-home hub paired; call /link/start first")
    ref = req.hub_ref or hubs[0]["account"]
    try:
        ctl = conn.control_device(
            ref,
            device_id=req.device_id,
            action=req.action,
            value=req.value,
            idempotency_key=req.idempotency_key,
            approval_verifier=_approval_verifier(req.task_id, req.step_id, req.idempotency_key),
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

    return ControlResponse(
        device_id=ctl.device_id,
        action=ctl.action,
        value=ctl.value,
        result=ctl.result,
        idempotency_key=ctl.idempotency_key,
        replay=ctl.replay,
    )


@app.post("/unlink/{hub_ref}")
def unlink(hub_ref: str) -> dict:
    conn = get_connector()
    try:
        conn.unlink_hub(hub_ref)
    except ConnectionMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connector": "smart_home", "hub_ref": hub_ref, "unlinked": True}


def _status_dict(d: Any) -> dict:
    return {
        "device_id": d.device_id,
        "name": d.name,
        "category": d.category,
        "state": d.state,
        "detail": d.detail,
    }
