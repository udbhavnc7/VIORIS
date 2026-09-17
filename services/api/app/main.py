"""FastAPI endpoints for Vioris API."""

import json
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from packages.shared.dialogue import DialogueOrchestrator
from packages.shared.permission_engine import PermissionEngine

app = FastAPI(title="Vioris API", version="0.1.0")

# Shared instances
permission_engine = PermissionEngine()
dialogue_orchestrator = DialogueOrchestrator()

# In-memory approval store (simplified for API)
_approvals: dict[str, dict] = {}

# WebSocket hub (imported from websocket module)
from services.api.app.websocket import WebSocketHub
ws_hub = WebSocketHub()


# --- Request/Response Models ---


class VoiceCommandRequest(BaseModel):
    text: str
    call_id: Optional[str] = None
    user_id: str = "default"


class VoiceCommandResponse(BaseModel):
    response: str
    decomposed: list[dict] = Field(default_factory=list)
    actionables: list[dict] = Field(default_factory=list)
    trust_level: str = "unverified"
    requires_approval: bool = False
    call_id: Optional[str] = None


class ApprovalRequest(BaseModel):
    action_id: str
    approved: bool
    user_id: str = "default"


class ApprovalResponse(BaseModel):
    action_id: str
    status: str
    message: str


class CallStartRequest(BaseModel):
    source: str = "incoming"
    caller_name: str
    caller_id: str = ""


class CallStartResponse(BaseModel):
    call_id: str
    status: str
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Any = None
    error: Optional[str] = None


class DigestRequest(BaseModel):
    call_id: str
    items: list[dict] = Field(default_factory=list)


# --- Endpoints ---


@app.post("/api/voice", response_model=VoiceCommandResponse)
async def process_voice_command(req: VoiceCommandRequest):
    result = dialogue_orchestrator.process(
        utterance=req.text,
        tool_results=[],
    )

    return VoiceCommandResponse(
        response=result.composed_response if result.composed_response else "Done.",
        decomposed=[{
            "text": i.text,
            "targets": i.targets,
            "type": i.intent_type,
            "negated": i.negated,
        } for i in result.sub_intents],
        actionables=[{
            "item": a.item,
            "signal": a.signal,
            "action": a.suggested_action,
        } for a in result.actionables],
        trust_level=result.trust_level.value,
        requires_approval=result.requires_approval,
        call_id=req.call_id,
    )


@app.post("/api/approve", response_model=ApprovalResponse)
async def approve_action(req: ApprovalRequest):
    record = _approvals.get(req.action_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Action {req.action_id} not found")

    record["status"] = "approved" if req.approved else "rejected"
    record["decided_at"] = datetime.utcnow().isoformat()

    return ApprovalResponse(
        action_id=req.action_id,
        status=record["status"],
        message=f"Action {req.action_id} {record['status']}.",
    )


@app.get("/api/approve/{action_id}", response_model=TaskStatusResponse)
async def get_approval_status(action_id: str):
    record = _approvals.get(action_id)
    if not record:
        raise HTTPException(status_code=404, detail="Action not found")
    return TaskStatusResponse(
        task_id=action_id,
        status=record["status"],
    )


@app.post("/api/call/start", response_model=CallStartResponse)
async def start_call(req: CallStartRequest):
    call_id = str(uuid.uuid4())[:12]
    return CallStartResponse(
        call_id=call_id,
        status="ringing",
        message=f"Call {call_id} initiated for {req.caller_name}.",
    )


@app.get("/api/call/{call_id}")
async def get_call_status(call_id: str):
    return {"call_id": call_id, "status": "active", "updated_at": datetime.utcnow().isoformat()}


@app.post("/api/call/{call_id}/end")
async def end_call(call_id: str):
    return {"call_id": call_id, "status": "ended", "ended_at": datetime.utcnow().isoformat()}


@app.post("/api/digest")
async def get_digest(req: DigestRequest):
    from packages.shared.call_experience import ProactiveDigest, DigestItem
    items = [DigestItem(**i) for i in req.items]
    digest = ProactiveDigest(call_id=req.call_id, items=items)
    return {"call_id": req.call_id, "spoken": digest.compose_spoken(), "items": req.items}


@app.post("/api/react")
async def email_react(email_id: str, reaction: str, sender: str = "unknown"):
    from packages.shared.call_experience import EmailReact
    react = EmailReact(email_id=email_id, sender=sender, subject="", reaction=reaction)
    valid, err = react.validate()
    if not valid:
        raise HTTPException(status_code=400, detail=err)
    return {"email_id": email_id, "reaction": reaction, "status": "sent"}


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "0.1.0",
        "connected_devices": ws_hub.connected_count,
        "timestamp": datetime.utcnow().isoformat(),
    }


# --- WebSocket Endpoint ---


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str, device_type: str = "phone"):
    await websocket.accept()
    ws_hub.register(client_id, device_type)

    try:
        while True:
            data = await websocket.receive_text()
            await ws_hub.receive(client_id, data)

            msg = json.loads(data)
            msg_type = msg.get("type", "")

            if msg_type == "audio":
                await websocket.send_text(json.dumps({
                    "type": "audio_ack",
                    "payload": {"received": True},
                    "timestamp": time.time(),
                }))
            elif msg_type == "approve":
                action_id = msg.get("payload", {}).get("action_id", "")
                approved = msg.get("payload", {}).get("approved", False)
                if approved:
                    approval_manager.approve(action_id)
                else:
                    approval_manager.reject(action_id)
                await websocket.send_text(json.dumps({
                    "type": "approval_result",
                    "payload": {"action_id": action_id, "approved": approved},
                    "timestamp": time.time(),
                }))
            elif msg_type == "ping":
                await websocket.send_text(json.dumps({
                    "type": "pong",
                    "payload": {},
                    "timestamp": time.time(),
                }))
    except WebSocketDisconnect:
        ws_hub.unregister(client_id)
