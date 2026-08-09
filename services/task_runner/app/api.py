"""
Task Runner API (Phase 2).

FastAPI surface for the exit test: pause/resume/retry/status via API call.
Backs onto the same TaskManager/engine as the CLI.

Run:
    uvicorn services.task_runner.app.api:app --port 8421
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from packages.shared.schemas import RiskTier, TaskStep

from .manager import TaskManager

app = FastAPI(title="Vioris Task Runner")

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
    if req.steps:
        steps = [TaskStep(**s) for s in req.steps]
    elif req.tool:
        steps = [
            TaskStep(agent="computer", tool=req.tool, risk_level=RiskTier.OBSERVE)
        ] * req.step_count
    else:
        steps = [
            TaskStep(agent="computer", tool="system.open_app", risk_level=RiskTier.OBSERVE)
        ] * req.step_count
    task = get_manager().create_task(req.request, steps)
    return _task_dict(task)


@app.get("/tasks")
def list_tasks() -> list[dict]:
    return [_task_dict(t) for t in get_manager().list_tasks()]


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    return _task_dict(get_manager().get(task_id))


def _execute(step) -> dict:
    """In-process stand-in executor. Phase 3 replaces this with the real
    computer/browser agents."""
    return {"ok": True, "tool": step.tool}


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
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/resume")
def resume_task(task_id: str) -> dict:
    try:
        return _task_dict(get_manager().resume(task_id))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    return _task_dict(get_manager().cancel(task_id))


@app.post("/tasks/{task_id}/retry/{step_id}")
def retry_step(task_id: str, step_id: str) -> dict:
    try:
        return _task_dict(get_manager().retry_step(task_id, step_id))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/tasks/{task_id}/approvals")
def list_approvals(task_id: str) -> list[dict]:
    return get_manager().pending_approvals(task_id)


@app.post("/approvals/{approval_id}/approve")
def approve_approval(approval_id: str, task_id: str | None = None) -> dict:
    mgr = get_manager()
    approval = mgr.approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    task = mgr.approve(approval.task_id, approval_id)
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
            }
            for s in task.steps
        ],
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }
