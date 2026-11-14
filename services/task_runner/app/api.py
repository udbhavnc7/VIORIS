"""
Task Runner API (Phase 2).

FastAPI surface for the exit test: pause/resume/retry/status via API call.
Backs onto the same TaskManager/engine as the CLI.

Run:
    uvicorn services.task_runner.app.api:app --port 8421
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from packages.shared.permission_engine import (
    PermissionEngine,
    UnknownToolError,
    register_all_tools,
)
from packages.shared.schemas import RiskTier, TaskStep
from packages.shared.second_factor import load_or_create_key
from packages.shared.second_factor import verify as verify_second_factor

from .manager import TaskManager


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The task-runner executes and gates steps; it needs the COMPLETE static
    # registry. Without this every Execute step is treated as unknown/blocked.
    if not PermissionEngine._frozen:
        register_all_tools()
        PermissionEngine.freeze()
    yield


app = FastAPI(title="Vioris Task Runner", lifespan=lifespan)

_manager: TaskManager | None = None


def get_manager() -> TaskManager:
    global _manager
    if _manager is None:
        from pathlib import Path

        _manager = TaskManager(Path("vioris_data/task_runner.db"))
    return _manager


class CreateTaskRequest(BaseModel):
    request: str
    steps: list[dict] | None = None
    tool: str | None = None  # convenience: N steps all with this tool
    step_count: int = 1


class StepPayload(BaseModel):
    agent: str = "computer"
    tool: str
    risk_level: RiskTier = RiskTier.OBSERVE


@app.post("/tasks")
def create_task(req: CreateTaskRequest) -> dict:
    """Create a task from an explicit plan or by asking the planner.

    Risk is ALWAYS taken from the frozen registry. A caller that sends
    `risk_level: "observe"` for an Execute tool does not get a downgrade —
    the registry wins, and an unknown tool is refused outright.
    """
    blocked_tools: list[dict] = []
    if req.steps:
        steps = []
        for raw in req.steps:
            tool = raw.get("tool")
            try:
                tier = PermissionEngine.classify(tool).tier
            except UnknownToolError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            steps.append(
                TaskStep(
                    agent=raw.get("agent", "system"),
                    tool=tool,
                    risk_level=tier,
                    result=raw.get("result") or {"args": raw.get("args") or {}},
                )
            )
    elif req.tool:
        try:
            tier = PermissionEngine.classify(req.tool).tier
        except UnknownToolError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        steps = [
            TaskStep(
                agent="system",
                tool=req.tool,
                risk_level=tier,
                result={"args": {}},
            )
            for _ in range(max(1, req.step_count))
        ]
    else:
        # Natural language: plan deterministically where possible, else use
        # the local LLM planner. Nothing executes during planning.
        from services.orchestrator.app.llm_client import PlannerBackendError
        from services.orchestrator.app.planner import Planner

        plan = Planner().plan(req.request)
        if plan.error:
            detail = (
                "No deterministic workflow matched and the local planner is "
                f"unavailable ({plan.error}). Install Ollama or use a suggested "
                "workflow."
            )
            raise HTTPException(status_code=422, detail=detail)
        steps = plan.steps
        blocked_tools = plan.blocked_tools
        if not steps:
            raise HTTPException(
                status_code=422,
                detail=(
                    "I couldn't turn that into a plan with the tools I have. "
                    "Try a specific request such as 'brief me', 'check unread mail', "
                    "or 'send a message to Disha saying I am running late'."
                ),
            )

    task = get_manager().create_task(req.request, steps)
    payload = _task_dict(task)
    payload["blocked_tools"] = blocked_tools
    return payload


@app.get("/tasks")
def list_tasks() -> list[dict]:
    return [_task_dict(t) for t in get_manager().list_tasks()]


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    return _task_dict(get_manager().get(task_id))


def _execute(step, task=None):
    """Dispatch an approved step to the owning agent daemon or connector.

    `task` is threaded through from the engine so Execute-tier connector steps
    (smart_home.control, …) can prove their approval belongs to this task's
    step before anything external fires.
    """
    from .executor import make_executor

    return make_executor()(step, task=task)


@app.post("/tasks/{task_id}/run")
def run_task(task_id: str) -> dict:
    task, outcome = get_manager().run_next_step(task_id, _execute)
    return {
        "task": _task_dict(task),
        "step": {
            "step_id": outcome.step.step_id if outcome.step else None,
            "tool": outcome.step.tool if outcome.step else None,
            "ok": outcome.ok,
            "error": outcome.error,
        },
    }


@app.post("/tasks/{task_id}/start")
def start_task(task_id: str) -> dict:
    return _task_dict(get_manager().start(task_id))


@app.post("/tasks/{task_id}/pause")
def pause_task(task_id: str) -> dict:
    try:
        return _task_dict(get_manager().pause(task_id))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/resume")
def resume_task(task_id: str) -> dict:
    try:
        return _task_dict(get_manager().resume(task_id))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    return _task_dict(get_manager().cancel(task_id))


@app.post("/tasks/{task_id}/retry/{step_id}")
def retry_step(task_id: str, step_id: str) -> dict:
    try:
        return _task_dict(get_manager().retry_step(task_id, step_id))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/tasks/{task_id}/approvals")
def list_approvals(task_id: str) -> list[dict]:
    return get_manager().pending_approvals(task_id)


@app.get("/approvals")
def list_all_approvals() -> list[dict]:
    """Every pending approval across all tasks (Power the mobile queue)."""
    return get_manager().pending_approvals()


@app.post("/stop-everything")
def stop_everything() -> dict:
    """Emergency stop: halt every non-terminal task (Stop Everything control)."""
    affected = get_manager().stop_all()
    return {"stopped": affected, "count": len(affected)}


CRITICAL_COOLDOWN_SECONDS = 5


@app.post("/approvals/{approval_id}/approve")
def approve_approval(
    approval_id: str,
    task_id: str | None = None,
    x_vioris_second_factor: str | None = Header(default=None, alias="X-Vioris-Second-Factor"),
) -> dict:
    mgr = get_manager()
    approval = mgr.approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")

    # Critical actions need a second factor AND a cooldown. Both are checked
    # here, at the API boundary, so a direct task-runner call cannot bypass
    # the gateway's PIN prompt.
    try:
        tier = PermissionEngine.classify(approval.tool).tier
    except UnknownToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if tier == RiskTier.CRITICAL:
        created = _parse_timestamp(approval.created_at)
        elapsed = _now_ts() - created
        if elapsed < CRITICAL_COOLDOWN_SECONDS:
            retry_after = max(1, round(CRITICAL_COOLDOWN_SECONDS - elapsed))
            raise HTTPException(
                status_code=409,
                detail=f"Critical cooldown: wait {retry_after}s before approving.",
            )
        key = load_or_create_key()
        ok, note = verify_second_factor(x_vioris_second_factor, approval_id, key)
        if not ok:
            raise HTTPException(status_code=403, detail=note)

    try:
        task = mgr.approve(approval.task_id, approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Auto-run the armed step: "approve from your phone while the laptop is
    # out of sight" must actually FIRE the step. The step was re-armed from
    # WAITING_APPROVAL to CREATED by approve(); run_next_step executes it with
    # the real executor and continues until the next gate or completion.
    task, _ = mgr.run_next_step(task.task_id, _execute)
    return {
        "approved": approval_id,
        "task_id": task.task_id,
        "status": task.status.value,
        "diff_card": approval.diff_card,
    }


@app.post("/approvals/{approval_id}/reject")
def reject_approval(approval_id: str) -> dict:
    mgr = get_manager()
    approval = mgr.approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    task = mgr.reject(approval.task_id, approval_id)
    return {"rejected": approval_id, "task_id": task.task_id, "status": task.status.value}


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    """Stream pending_approval / approved / rejected events live.

    The events hub is process-local, so any mutation through this API — or the
    CLI running in the same process — pushes here. Clients see the compact wire
    form of each approval event.
    """
    await websocket.accept()
    mgr = get_manager()
    sub = mgr.events.subscribe(asyncio.get_running_loop())
    try:
        while True:
            event = await sub.queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        mgr.events.unsubscribe(sub)


@app.get("/audit-events")
def audit_events(limit: int = 100) -> dict:
    mgr = get_manager()
    return {"events": mgr.audit_events_copy(limit), "chain_ok": mgr.verify_chain()[0]}


def _now_ts() -> float:
    return time.time()


def _parse_timestamp(value: str | None) -> float:
    """Approval rows store UTC timestamps from SQLite's datetime('now')."""
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _task_dict(task) -> dict:
    return {
        "task_id": task.task_id,
        "request": task.request,
        "status": task.status.value,
        "risk_level": task.risk_level.value if task.risk_level else None,
        "steps": [
            {
                "step_id": s.step_id,
                "agent": s.agent,
                "tool": s.tool,
                "risk_level": s.risk_level.value,
                "status": s.status.value,
                "error": s.error,
                # Before execution this holds {"args": …}; afterwards it holds
                # the verified tool result. The UI only reads it for detail.
                "result": s.result,
                "created_at": s.created_at.isoformat(),
            }
            for s in task.steps
        ],
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }
