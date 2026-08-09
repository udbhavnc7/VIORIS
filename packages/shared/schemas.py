"""
Vioris shared data schemas.

Canonical shapes for Task, TaskStep, AuditEvent, and permission-related models.
All models use Pydantic v2 for validation and serialization.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─── Risk Tiers ───────────────────────────────────────────────────────────────


class RiskTier(str, Enum):
    """Four-level permission model. Assigned statically per tool, never by the LLM."""

    OBSERVE = "observe"
    PREPARE = "prepare"
    EXECUTE = "execute"
    CRITICAL = "critical"


# ─── Task & Step ──────────────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STOPPED = "stopped"


class TaskStep(BaseModel):
    """A single step within a task plan."""

    step_id: str = Field(default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}")
    agent: str
    tool: str
    risk_level: RiskTier
    status: TaskStatus = TaskStatus.CREATED
    result: dict[str, Any] | None = None
    error: str | None = None
    idempotency_key: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Task(BaseModel):
    """A user request decomposed into ordered, risk-tagged steps."""

    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    request: str
    status: TaskStatus = TaskStatus.CREATED
    risk_level: RiskTier = RiskTier.OBSERVE
    steps: list[TaskStep] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    audit_events: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Audit Events ─────────────────────────────────────────────────────────────


class AuditAction(str, Enum):
    """Every possible action recorded in the audit log."""

    PLANNED_STEP = "planned_step"
    REQUESTED_APPROVAL = "requested_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STOPPED = "stopped"
    WAKE_ACTIVATED = "wake_activated"
    STATE_TRANSITION = "state_transition"
    COMMAND_RECEIVED = "command_received"
    COMMAND_COMPLETED = "command_completed"


class AuditEvent(BaseModel):
    """Append-only, hash-chained audit record. Never edited or deleted."""

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    task_id: str | None = None
    actor: str  # 'system' | 'user' | 'agent:<name>'
    action: AuditAction
    detail: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str | None = None
    hash: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Permission Engine Types ─────────────────────────────────────────────────


class ToolRegistration(BaseModel):
    """Static registration of a tool's risk classification."""

    tool_name: str
    tier: RiskTier
    confirmation_required: bool
    description: str
    # Execute/Critical tools must define what the diff card shows
    diff_card_fields: list[str] = Field(default_factory=list)


class PermissionResult(BaseModel):
    """Result of classifying an action through the permission engine."""

    tool_name: str
    tier: RiskTier
    confirmation_required: bool
    requires_second_factor: bool = False
    cooldown_seconds: int = 0
    allowed: bool = True
    reason: str = ""


# ─── Agent State ──────────────────────────────────────────────────────────────


class AgentState(str, Enum):
    """Desktop agent state machine states."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"
    STOPPED = "stopped"
