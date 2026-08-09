"""WebSocket approval events (Prompt 2.3): pending_approval -> approved/rejected."""

import pytest
from fastapi.testclient import TestClient

from packages.shared.permission_engine import PermissionEngine, register_phase1_tools
from packages.shared.permission_engine import register_phase2_tools

from services.task_runner.app.api import app


@pytest.fixture(autouse=True)
def _full_registry():
    PermissionEngine.reset()
    register_phase1_tools()
    register_phase2_tools()
    yield
    PermissionEngine.reset()


@pytest.fixture
def client():
    # Point the API at a throwaway DB so tests don't write real state.
    from services.task_runner.app import api
    from services.task_runner.app.manager import TaskManager

    import tempfile
    from pathlib import Path

    api._manager = TaskManager(Path(tempfile.mkdtemp()) / "t.db")
    with TestClient(app) as c:
        yield c


def _create_send_task(client) -> dict:
    resp = client.post(
        "/tasks",
        json={
            "request": "send nudge",
            "steps": [
                {
                    "agent": "system",
                    "tool": "system.send_message",
                    "risk_level": "execute",
                    "result": {"args": {"recipient": "alice", "content": "hi", "channel": "sms"}},
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_websocket_streams_pending_approval_then_approved(client):
    task = _create_send_task(client)
    task_id = task["task_id"]
    client.post(f"/tasks/{task_id}/start")

    with client.websocket_connect("/ws/events") as ws:
        resp = client.post(f"/tasks/{task_id}/run", json={})
        assert resp.status_code == 200, resp.text

        event = ws.receive_json()
        assert event["type"] == "pending_approval"
        assert event["task_id"] == task_id
        assert event["tool"] == "system.send_message"
        assert event["diff_card"] == {
            "recipient": "alice",
            "content": "hi",
            "channel": "sms",
        }

        approval_id = event["approval_id"]
        client.post(f"/approvals/{approval_id}/approve")
        event = ws.receive_json()
        assert event["type"] == "approved"
        assert event["approval_id"] == approval_id


def test_ws_streams_pending_approval_then_rejected(client):
    task = _create_send_task(client)
    task_id = task["task_id"]
    client.post(f"/tasks/{task_id}/start")

    with client.websocket_connect("/ws/events") as ws:
        client.post(f"/tasks/{task_id}/run", json={})
        event = ws.receive_json()
        assert event["type"] == "pending_approval"
        approval_id = event["approval_id"]

        client.post(f"/approvals/{approval_id}/reject")
        event = ws.receive_json()
        assert event["type"] == "rejected"
        assert event["approval_id"] == approval_id