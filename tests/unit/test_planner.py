import pytest

from packages.shared.permission_engine import PermissionEngine, register_phase1_tools

from services.orchestrator.app.llm_client import (
    OllamaBackend,
    PlannerBackendError,
    _extract_tool_calls,
)
from services.orchestrator.app.planner import Planner
from services.orchestrator.app.tool_schema import build_tool_schema

register_phase1_tools()


@pytest.fixture(autouse=True)
def frozen_registry():
    PermissionEngine.reset()
    register_phase1_tools()
    PermissionEngine.freeze()
    yield
    PermissionEngine.reset()  # don't leave the registry frozen for later modules


class FakeBackend:
    """Deterministic planner backend returning canned tool_calls."""

    def __init__(self, calls=None, error=None, no_tools=False):
        self.calls = calls or []
        self.error = error
        self.no_tools = no_tools

    def chat_with_tools(self, messages, tools):
        if self.error:
            raise PlannerBackendError(self.error)
        if self.no_tools:
            return {"tool_calls": []}
        return {"tool_calls": self.calls}


class TestPlannerRiskTags:
    def test_registered_tools_tagged_with_registry_risk(self):
        backend = FakeBackend(
            calls=[
                {"name": "system.open_app", "arguments": {"app": "vs code"}},
                {"name": "system.get_time", "arguments": {}},
            ]
        )
        plan = Planner(backend).plan("open vs code and tell me the time")
        assert len(plan.steps) == 2
        assert plan.steps[0].tool == "system.open_app"
        assert plan.steps[0].risk_level.value == "observe"
        assert plan.steps[0].result == {"args": {"app": "vs code"}}
        assert plan.steps[1].risk_level.value == "observe"

    def test_unregistered_tools_flagged_not_steps(self):
        backend = FakeBackend(
            calls=[
                {"name": "email.delete_all", "arguments": {}},
                {"name": "account.wipe", "arguments": {}},
            ]
        )
        plan = Planner(backend).plan("delete all mail and wipe my account")
        assert plan.steps == []
        assert {b["tool"] for b in plan.blocked_tools} == {"email.delete_all", "account.wipe"}
        assert all("refused" in b["reason"] for b in plan.blocked_tools)

    def test_mixed_keeps_registered_flags_unknown(self):
        backend = FakeBackend(
            calls=[
                {"name": "system.set_reminder", "arguments": {"reminder": "call mom", "at": "6pm"}},
                {"name": "email.delete_all", "arguments": {}},
            ]
        )
        plan = Planner(backend).plan("remind me to call mom at 6pm and delete my emails")
        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "system.set_reminder"
        assert plan.steps[0].risk_level.value == "prepare"  # registry PREPARE tier
        assert plan.blocked_tools == [
            {"tool": "email.delete_all", "reason": "not in the frozen registry — refused"}
        ]

    def test_llm_risk_label_is_ignored(self):
        # If the model smuggles a risk_level in arguments, it must not end up
        # on the TaskStep — the registry alone decides.
        backend = FakeBackend(
            calls=[{"name": "system.open_app", "arguments": {"app": "x", "risk_level": "critical"}}]
        )
        plan = Planner(backend).plan("open x")
        assert plan.steps[0].risk_level.value == "observe"

    def test_plan_is_json_serializable_before_execution(self):
        backend = FakeBackend(calls=[{"name": "system.get_time", "arguments": {}}])
        plan = Planner(backend).plan("what time is it")
        d = plan.to_dict()
        assert d["steps"][0]["tool"] == "system.get_time"
        assert d["steps"][0]["risk_level"] == "observe"
        import json

        json.dumps(d)  # must not raise


class TestPlannerEdgeCases:
    def test_backend_failure_returns_plan_with_error(self):
        plan = Planner(FakeBackend(error="ollama down")).plan("set a timer")
        assert plan.steps == []
        assert plan.error and "ollama down" in plan.error

    def test_no_tools_returned_is_empty_plan(self):
        plan = Planner(FakeBackend(no_tools=True)).plan("do something unsupported")
        assert plan.steps == []
        assert plan.blocked_tools == []
        assert plan.error is None


class TestToolSchema:
    def test_schema_derived_from_registry(self):
        schema = build_tool_schema()
        names = {f["function"]["name"] for f in schema}
        assert "system.open_app" in names
        assert "system.stop" in names
        assert "email.delete_all" not in names  # not registered -> not offered


class TestOllamaToolCallExtraction:
    def test_extracts_from_tool_calls(self):
        data = {
            "message": {"tool_calls": [{"function": {"name": "system.get_time", "arguments": {}}}]}
        }
        assert _extract_tool_calls(data, "test-model") == [
            {"name": "system.get_time", "arguments": {}}
        ]

    def test_falls_back_to_json_in_content(self):
        data = {
            "message": {"content": '[{"name": "system.open_app", "arguments": {"app": "chrome"}}]'}
        }
        assert _extract_tool_calls(data, "test-model") == [
            {"name": "system.open_app", "arguments": {"app": "chrome"}}
        ]

    def test_empty_message_gives_no_calls(self):
        assert _extract_tool_calls({"message": {}}, "m") == []


def test_ollama_backend_imports_cleanly():
    assert OllamaBackend is not None
