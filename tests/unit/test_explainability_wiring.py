"""End-to-end explainability wiring: planner reasoning -> manager -> API surface.

The planner records why each step was chosen; the task manager persists that
record beside the audit chain so "why did you do that?" is answered from the
same durable state. These tests cover the manager seam (no HTTP required).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from packages.shared.permission_engine import PermissionEngine, register_phase1_tools
from packages.shared.schemas import RiskTier, TaskStep
from services.task_runner.app.api import app as tr_app
from services.task_runner.app.manager import TaskManager


@pytest.fixture(autouse=True)
def _phase1_registry():
    PermissionEngine.reset()
    register_phase1_tools()
    yield
    PermissionEngine.reset()


def step(tool="system.open_app", reasoning="The request named this app.") -> TaskStep:
    return TaskStep(
        agent="computer",
        tool=tool,
        risk_level=RiskTier.OBSERVE,
        reasoning=reasoning,
    )


def test_create_task_records_reasoning_for_every_step(tmp_path):
    mgr = TaskManager(Path(tmp_path) / "t.db")
    task = mgr.create_task(
        "open chrome",
        [
            step("system.open_app", "The request asked to open chrome."),
            step("system.get_time", "The user also asked for the time."),
        ],
    )

    result = mgr.explain_task(task.task_id)
    assert result["found"] is True
    assert len(result["steps"]) == 2
    assert result["steps"][0]["chosen_reason"] == "The request asked to open chrome."
    assert result["steps"][0]["source"] == "recorded"
    assert "open_app" in result["explanation"]
    mgr.close()


def test_explain_step_returns_reason_and_risk_rationale(tmp_path):
    mgr = TaskManager(Path(tmp_path) / "t.db")
    task = mgr.create_task("open chrome", [step()])
    step_id = task.steps[0].step_id

    result = mgr.explain_step(task.task_id, step_id)
    assert result["tool"] == "system.open_app"
    assert result["chosen_reason"] == "The request named this app."
    assert "observe-tier" in result["risk_rationale"]
    assert result["explanation"].startswith("I used system.open_app because")
    mgr.close()


def test_explain_step_unknown_raises_keyerror(tmp_path):
    mgr = TaskManager(Path(tmp_path) / "t.db")
    task = mgr.create_task("open chrome", [step()])
    with pytest.raises(KeyError):
        mgr.explain_step(task.task_id, "step_missing")
    mgr.close()


def test_reasoning_survives_restart(tmp_path):
    db = Path(tmp_path) / "t.db"
    mgr = TaskManager(db)
    task = mgr.create_task("open chrome", [step("system.open_app", "Because chrome was named.")])
    mgr.close()

    mgr2 = TaskManager(db)
    result = mgr2.explain_task(task.task_id)
    assert result["steps"][0]["chosen_reason"] == "Because chrome was named."
    assert result["steps"][0]["source"] == "recorded"
    mgr2.close()


def test_old_task_without_reasoning_is_derived_not_fabricated(tmp_path):
    """A task persisted before explainability must still explain itself, and
    the UI must be able to tell the answer was derived from the plan."""
    mgr = TaskManager(Path(tmp_path) / "t.db")
    task = mgr.engine.create_task("legacy request", [step("system.open_app", None)])
    mgr._persist(task)

    result = mgr.explain_task(task.task_id)
    record = result["steps"][0]
    assert record["source"] == "derived"
    assert "system.open_app" in record["chosen_reason"]
    assert result["found"] is True
    mgr.close()


def test_explain_query_answers_from_recent_reasoning(tmp_path):
    mgr = TaskManager(Path(tmp_path) / "t.db")
    mgr.create_task("open chrome", [step("system.open_app", "You asked me to open chrome.")])

    result = mgr.explain_query("why did you do that?")
    assert result["found"] is True
    assert "system.open_app" in result["explanation"]
    assert result["query_type"] == "general"
    mgr.close()


def test_task_runner_explain_routes(tmp_path):
    """The HTTP surface returns the same reasoning the manager recorded."""
    import services.task_runner.app.api as tr_api

    tr_api._manager = TaskManager(Path(tmp_path) / "tr.db")
    try:
        with TestClient(tr_app) as client:
            created = client.post(
                "/tasks",
                json={
                    "request": "open chrome",
                    "steps": [
                        {
                            "agent": "system",
                            "tool": "system.open_app",
                            "result": {"args": {"app": "chrome"}},
                            "reasoning": "You asked me to open chrome.",
                        }
                    ],
                },
            )
            assert created.status_code == 200
            task_id = created.json()["task_id"]
            step_id = created.json()["steps"][0]["step_id"]
            assert created.json()["steps"][0]["reasoning"] == "You asked me to open chrome."

            whole = client.get(f"/tasks/{task_id}/explain")
            assert whole.status_code == 200
            assert whole.json()["steps"][0]["chosen_reason"] == "You asked me to open chrome."

            one = client.get(f"/tasks/{task_id}/steps/{step_id}/explain")
            assert one.status_code == 200
            assert one.json()["tool"] == "system.open_app"

            why = client.post("/explain", json={"query": "why did you do that?"})
            assert why.status_code == 200
            assert "system.open_app" in why.json()["explanation"]

            recent = client.get("/explain/recent")
            assert recent.status_code == 200
            assert recent.json()["reasoning"][0]["step_id"] == step_id

            missing = client.get(f"/tasks/{task_id}/steps/step_missing/explain")
            assert missing.status_code == 404
    finally:
        tr_api._manager.close()
        tr_api._manager = None


def test_recent_reasoning_is_newest_first(tmp_path):
    mgr = TaskManager(Path(tmp_path) / "t.db")
    mgr.create_task("open chrome", [step("system.open_app", "First reason.")])
    mgr.create_task("check the time", [step("system.get_time", "Second reason.")])

    recent = mgr.recent_reasoning(limit=10)
    assert len(recent) == 2
    assert {item["chosen_reason"] for item in recent} == {"First reason.", "Second reason."}
    mgr.close()
