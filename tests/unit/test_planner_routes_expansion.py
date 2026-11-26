"""Deterministic planner routes for smart-home, knowledge, telephony and agents.

These routes must work with no LLM installed and must take their risk tier from
the frozen permission registry, never from the utterance.
"""

from __future__ import annotations

import pytest

from packages.shared.permission_engine import PermissionEngine, register_all_tools
from services.orchestrator.app.planner import Planner


class CountingBackend:
    """Records fallthrough to the LLM so tests can assert offline routing."""

    def __init__(self):
        self.calls = 0

    def chat_with_tools(self, messages, tools):
        self.calls += 1
        return {"tool_calls": []}


@pytest.fixture(autouse=True)
def frozen_registry():
    PermissionEngine.reset()
    register_all_tools()
    PermissionEngine.freeze()
    yield
    PermissionEngine.reset()


@pytest.fixture
def planner():
    backend = CountingBackend()
    return Planner(backend=backend, deterministic=True), backend


@pytest.mark.parametrize(
    "utterance,tool,args,tier",
    [
        ("check the smart home devices", "smart_home.device_status", {}, "observe"),
        ("check my notes", "notes.list_recent", {}, "observe"),
        ("show my recent cloud files", "cloud_files.list_recent", {}, "observe"),
        ("search bookmarks for python", "bookmarks.search", {"query": "python"}, "observe"),
        ("check my browser history", "history.recent", {}, "observe"),
        ("list open windows", "computer.list_windows", {}, "observe"),
        ("take a screenshot", "computer.capture_screenshot", {}, "observe"),
        ("read the screen", "computer.read_screen_text", {}, "observe"),
        ("read the page", "browser.read_page", {}, "observe"),
        ("search files for budget", "file.search_files", {"query": "budget"}, "observe"),
    ],
)
def test_observe_routes_are_offline_and_read_only(planner, utterance, tool, args, tier):
    engine, backend = planner
    plan = engine.plan(utterance)
    assert plan.error is None
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.tool == tool
    assert step.risk_level.value == tier
    for key, value in args.items():
        assert step.result["args"][key] == value
    assert backend.calls == 0


def test_turn_off_light_is_execute_tier_with_exact_device(planner):
    engine, backend = planner
    plan = engine.plan("turn off the living room light")
    step = plan.steps[0]
    assert step.tool == "smart_home.control"
    assert step.risk_level.value == "execute"
    assert step.result["args"]["action"] == "off"
    assert step.result["args"]["device_id"] == "light.living_room"
    assert backend.calls == 0


def test_prepare_call_is_prepare_tier_and_never_dials(planner):
    engine, backend = planner
    plan = engine.plan("prepare a call to the vet")
    step = plan.steps[0]
    assert step.tool == "telephony.prepare_call"
    assert step.risk_level.value == "prepare"
    assert "vet" in step.result["args"]["recipient"]
    assert backend.calls == 0


def test_navigate_is_observe_tier_and_keeps_url(planner):
    engine, backend = planner
    plan = engine.plan("navigate to https://example.com/docs")
    step = plan.steps[0]
    assert step.tool == "browser.navigate"
    assert step.risk_level.value == "observe"
    assert step.result["args"]["url"] == "https://example.com/docs"
    assert backend.calls == 0


def test_compound_request_is_not_narrowed_to_one_new_route(planner):
    engine, backend = planner
    engine.plan("check my notes and search bookmarks for python")
    # The compound guard must hand this back to the LLM rather than silently
    # dropping one of the two instructions.
    assert backend.calls == 1
