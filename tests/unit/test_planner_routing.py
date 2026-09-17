"""Tests for the planner's intent-based routing (Phase 7.1)."""

import pytest
from unittest.mock import MagicMock

from packages.shared.intent_classifier import IntentType
from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase1_tools,
    register_phase6_connector_tools,
)
from services.orchestrator.app.planner import Planner, Plan


@pytest.fixture(autouse=True)
def frozen_registry():
    PermissionEngine.reset()
    register_phase1_tools()
    register_phase6_connector_tools()
    PermissionEngine.freeze()
    yield
    PermissionEngine.reset()


class FakeBackend:
    """Fake LLM backend that returns predetermined tool calls."""

    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls or []
        self.call_count = 0

    def chat_with_tools(self, messages, tools):
        self.call_count += 1
        return {"tool_calls": self.tool_calls}


class TestDirectRoute:
    def test_time_query_skips_llm(self):
        backend = FakeBackend(tool_calls=[])
        planner = Planner(backend=backend)
        plan = planner.plan("what time is it")

        assert plan.request == "what time is it"
        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "system.get_time"
        assert plan.steps[0].risk_level.value == "observe"
        # LLM should NOT have been called
        assert backend.call_count == 0

    def test_stop_command_skips_llm(self):
        backend = FakeBackend(tool_calls=[])
        planner = Planner(backend=backend)
        plan = planner.plan("stop")

        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "system.stop"
        assert backend.call_count == 0

    def test_open_command_skips_llm(self):
        backend = FakeBackend(tool_calls=[])
        planner = Planner(backend=backend)
        plan = planner.plan("open chrome")

        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "system.open_app"
        assert plan.steps[0].result["args"]["app"] == "chrome"
        assert backend.call_count == 0

    def test_reminder_skips_llm(self):
        backend = FakeBackend(tool_calls=[])
        planner = Planner(backend=backend)
        plan = planner.plan("remind me to call mom at 3pm")

        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "system.set_reminder"
        assert "call mom" in plan.steps[0].result["args"]["reminder"]
        assert "3pm" in plan.steps[0].result["args"]["at"]
        assert backend.call_count == 0

    def test_remind_me_without_time(self):
        backend = FakeBackend(tool_calls=[])
        planner = Planner(backend=backend)
        plan = planner.plan("remind me to buy groceries")

        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "system.set_reminder"
        assert "buy groceries" in plan.steps[0].result["args"]["reminder"]
        assert backend.call_count == 0


class TestFullPlannerRoute:
    def test_complex_request_uses_llm(self):
        tool_calls = [
            {"name": "gmail.read_unread", "arguments": {"hours": 24}},
            {"name": "system.open_app", "arguments": {"app": "chrome"}},
        ]
        backend = FakeBackend(tool_calls=tool_calls)
        planner = Planner(backend=backend)
        plan = planner.plan("check my email and also open chrome")

        assert len(plan.steps) == 2
        assert plan.steps[0].tool == "gmail.read_unread"
        assert plan.steps[1].tool == "system.open_app"
        assert backend.call_count == 1

    def test_unknown_request_uses_llm(self):
        tool_calls = [{"name": "system.get_time", "arguments": {}}]
        backend = FakeBackend(tool_calls=tool_calls)
        planner = Planner(backend=backend)
        plan = planner.plan("banana random sentence")

        # Unknown intent falls through to LLM
        assert backend.call_count == 1

    def test_llm_failure_returns_error_plan(self):
        from services.orchestrator.app.llm_client import PlannerBackendError

        def failing_chat(messages, tools):
            raise PlannerBackendError("Ollama is down")

        backend = FakeBackend()
        backend.chat_with_tools = failing_chat
        planner = Planner(backend=backend)
        plan = planner.plan("do something complicated with email and calendar")

        assert plan.error is not None
        assert "Ollama" in plan.error


class TestPlanOutput:
    def test_plan_is_json_serializable(self):
        backend = FakeBackend(tool_calls=[])
        planner = Planner(backend=backend)
        plan = planner.plan("what time is it")

        import json
        serialized = json.dumps(plan.to_dict())
        assert isinstance(serialized, str)
        assert "what time is it" in serialized
