"""
Vioris API gateway — FastAPI entry point (Phase 1 skeleton).

Registers and freezes the permission registry on startup, exposes a health
check and read-only audit listing. All mutation endpoints (tasks, approvals,
etc.) arrive in later phases; this is the process the later prompts extend.

Run from the repo root:
    uvicorn services.api_gateway.app.main:app --reload --port 8420
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase1_tools,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup: register Phase 1 tools, then freeze the registry."""
    register_phase1_tools()
    PermissionEngine.freeze()
    yield


app = FastAPI(title="Vioris API Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """Liveness probe. Reports registry state, not DB health."""
    return {
        "status": "ok",
        "service": "api-gateway",
        "registry_frozen": PermissionEngine._frozen,
        "tools_registered": len(PermissionEngine.list_tools()),
    }


@app.get("/audit-events")
async def list_audit_events(limit: int = 100) -> dict:
    """Placeholder: real audit listing hits Postgres once the task-runner lands.

    Phase 1 only persists audit events in the local agent store; this endpoint
    documents the contract from docs/02-architecture.md (GET /audit-events).
    """
    return {"events": [], "limit": limit, "note": "DB-backed listing lands with the task-runner"}


@app.get("/")
async def root() -> dict:
    return {"service": "vioris-api-gateway", "docs": "/docs"}
