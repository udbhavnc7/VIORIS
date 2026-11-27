"""Deterministic planner routes for routine suggestions and acceptance."""

import pytest

from packages.shared.permission_engine import PermissionEngine, register_all_tools
from services.orchestrator.app.planner import Planner


@pytest.fixture(autouse=True)
def registry():
    PermissionEngine.reset()
    register_all_tools()
    PermissionEngine.freeze()
    yield
    PermissionEngine.reset()


@pytest.mark.parametrize(
    "transcript",
    [
        "suggest routines",
        "show me my routines",
        "what routines do you see?",
        "any routine suggestions?",
    ],
)
def test_suggestion_request_is_prepare_tier(transcript):
    plan = Planner().plan(transcript)
    assert [s.tool for s in plan.steps] == ["system.suggest_routine"]
    assert plan.steps[0].risk_level.value == "prepare"


def test_acceptance_request_is_execute_tier_with_diff_card_fields():
    plan = Planner().plan("automate routine seq_abc123_0")
    assert [s.tool for s in plan.steps] == ["system.create_routine"]
    step = plan.steps[0]
    assert step.risk_level.value == "execute"
    assert step.result["args"]["pattern_id"] == "seq_abc123_0"


def test_acceptance_without_a_pattern_id_is_not_planned():
    plan = Planner().plan("automate this routine")
    assert all(s.tool != "system.create_routine" for s in plan.steps)
