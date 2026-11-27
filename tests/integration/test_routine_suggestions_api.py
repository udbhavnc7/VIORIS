"""End-to-end opt-in routine suggestions: mining is Prepare, acceptance is Execute."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from packages.shared.permission_engine import PermissionEngine
from packages.shared.routine_settings import RoutineSettingsStore
from packages.shared.schemas import Task, TaskStatus, TaskStep
from services.task_runner.app import api
from services.task_runner.app.manager import TaskManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    PermissionEngine.reset()
    monkeypatch.setenv("VIORIS_DATA_DIR", str(tmp_path))
    api._manager = TaskManager(tmp_path / "runner.db")
    api._routine_settings = RoutineSettingsStore(tmp_path / "routines.json")
    with TestClient(api.app) as c:
        yield c
    PermissionEngine.reset()


def _seed_history(count=3):
    for i in range(count):
        task = Task(
            request="open chrome and mute notifications",
            status=TaskStatus.COMPLETED,
            steps=[
                TaskStep(agent="system", tool="system.open_app", risk_level="observe"),
                TaskStep(agent="system", tool="smart_home.control", risk_level="execute"),
            ],
            created_at=datetime.now(UTC).replace(hour=9) - timedelta(days=i),
        )
        api._manager.create_task(task.request, task.steps)


def test_suggestions_are_dark_until_opt_in(client):
    _seed_history()
    r = client.get("/routines/suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["opted_in"] is False
    assert body["suggestions"] == []
    assert body["count"] == 0


def test_opt_in_mines_prepare_suggestions_without_creating_automation(client):
    _seed_history()
    assert client.post("/routines/opt-in", json={"enabled": True}).json()["opted_in"] is True

    r = client.get("/routines/suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["opted_in"] is True
    assert body["count"] >= 1
    sequence_patterns = [s for s in body["suggestions"] if len(s["sequence"]) == 2]
    assert sequence_patterns, body["suggestions"]
    assert sequence_patterns[0]["sequence"] == ["system.open_app", "smart_home.control"]
    # Mining wrote no automation: the acceptance ledger stays empty.
    assert api._routine_settings.accepted() == {}


def test_acceptance_pauses_for_approval_and_only_records_after_it(client):
    _seed_history()
    client.post("/routines/opt-in", json={"enabled": True})
    suggestion = client.get("/routines/suggestions").json()["suggestions"][0]

    created = client.post(
        "/routines/create",
        json={
            "pattern_id": suggestion["pattern_id"],
            "description": suggestion["description"],
            "tool": suggestion["tool"],
            "sequence": suggestion["sequence"],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "waiting_approval"
    assert body["approval_id"]
    assert body["diff_card"]["pattern_id"] == suggestion["pattern_id"]
    assert body["diff_card"]["tool"] == suggestion["tool"]
    # Nothing recorded before approval.
    assert api._routine_settings.is_accepted(suggestion["pattern_id"]) is False

    approved = client.post(f"/approvals/{body['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"

    record = api._routine_settings.accepted()[suggestion["pattern_id"]]
    assert record["description"] == suggestion["description"]
    assert record["via"] == "system.create_routine"


def test_create_requires_opt_in(client):
    r = client.post("/routines/create", json={"pattern_id": "seq_abc_0"})
    assert r.status_code == 403
    assert "opt-in" in r.json()["detail"]
    assert api._routine_settings.accepted() == {}
