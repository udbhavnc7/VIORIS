"""
Planner (Phase 2, Prompt 2.2).

Takes a transcript and returns an ordered list of TaskSteps as a plan object —
nothing executes here. The LLM proposes tools + arguments + order; the risk
level of every step is pulled from the STATIC permission registry, never from
the model. Unregistered tools proposed by the LLM are flagged as blocked, not
silently allowed.

The plan is returned as JSON before anything runs, so a caller (orchestrator /
human) can review it. See docs/02-architecture.md §3 (Critic Agent reviews
plans before execution).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from packages.shared.permission_engine import PermissionEngine, UnknownToolError
from packages.shared.schemas import TaskStep

from .llm_client import OllamaBackend, PlannerBackendError
from .tool_schema import build_tool_schema

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Vioris's planner. Break the user request into an ordered list of "
    "steps using ONLY the provided tools. Reply with tool_calls only — one call "
    "per step, in execution order. Each tool_call must be exactly one of the "
    "offered tools; do not invent tools. If the request cannot be satisfied by "
    "the offered tools, make no tool calls. Never include risk levels or "
    "permission labels in your output."
)


@dataclass
class Plan:
    request: str
    steps: list[TaskStep] = field(default_factory=list)
    blocked_tools: list[dict] = field(default_factory=list)  # proposed but unregistered
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "request": self.request,
            "steps": [_step_dict(s) for s in self.steps],
            "blocked_tools": self.blocked_tools,
            "error": self.error,
        }


class Planner:
    def __init__(self, backend: callable | None = None) -> None:
        self.backend = backend or OllamaBackend()

    def plan(self, transcript: str) -> Plan:
        """Plan a transcript without executing anything. Returns JSON-able Plan."""
        tools = build_tool_schema()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ]
        try:
            result = self.backend.chat_with_tools(messages, tools)
        except PlannerBackendError as exc:
            logger.warning("planner backend failed: %s", exc)
            return Plan(request=transcript, error=str(exc))
        except UnknownToolError as exc:
            return Plan(request=transcript, error=str(exc))

        return self._to_plan(transcript, result.get("tool_calls", []))

    def _to_plan(self, transcript: str, tool_calls: list[dict]) -> Plan:
        plan = Plan(request=transcript)
        for i, call in enumerate(tool_calls):
            name = call.get("name")
            if not name:
                continue
            try:
                permission = PermissionEngine.classify(name)
            except UnknownToolError:
                plan.blocked_tools.append(
                    {"tool": name, "reason": "not in the frozen registry — refused"}
                )
                continue
            step = TaskStep(
                agent="system",
                tool=name,
                risk_level=permission.tier,  # static registry decides, never the LLM
                result={"args": call.get("arguments", {})},
            )
            plan.steps.append(step)
        return plan


def _step_dict(step: TaskStep) -> dict:
    return {
        "step_id": step.step_id,
        "agent": step.agent,
        "tool": step.tool,
        "risk_level": step.risk_level.value,
        "arguments": step.result,
        "status": step.status.value,
    }
