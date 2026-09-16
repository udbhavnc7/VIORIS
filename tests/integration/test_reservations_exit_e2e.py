"""Phase 6 exit e2e: reservations connector through the real task loop via the
generic connector router.

Boots the reservations connector API and the task-runner as real uvicorn
services sharing ONE approval ledger, then drives the Phase 6 loop for a
GENERIC connector tool (reservations.*) — proving the data-driven executor
router (`_CONNECTOR_ROUTES`) is a real end-to-end path, not just smart-home:

    search_slots (observe)  -> runs with no approval
    create (execute)        -> pauses at the approval gate with a diff card ->
                              approve -> executor dispatches with the exact
                              (task, step, idempotency_key) triple -> the
                              connector re-verifies the ledger record -> the
                              booking fires exactly once -> task completes.

Also proves the hard rule: an un-approved create never fires.
No component is stubbed over HTTP; only the local booking provider is the mock.
"""

from __future__ import annotations

import pytest
import requests

from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase6_connector_tools,
)
from tests.integration._server import Server, free_port


def _pair(connector_url: str, mock, session_ref: str = "sess-res") -> None:
    mock.complete_scan(session_ref)
    start = requests.post(f"{connector_url}/link/start")
    assert start.status_code == 200, start.text
    done = requests.post(f"{connector_url}/link/complete", params={"session_ref": session_ref})
    assert done.status_code == 200, done.text


def _create_task(base: str, tool: str, args: dict, risk: str, request: str) -> str:
    r = requests.post(
        f"{base}/tasks",
        json={
            "request": request,
            "steps": [
                {
                    "agent": "reservations",
                    "tool": tool,
                    "risk_level": risk,
                    "result": {"args": args},
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    """Boot the reservations connector + task-runner on free ports, sharing one
    approval ledger, with the mock booking provider injected."""
    from integrations.mock_providers.reservations_mock import MockReservationsTransport
    from integrations.reservations import api as res_api
    from integrations.reservations.connector import ReservationsConnector
    from services.task_runner.app import api as task_api
    from services.task_runner.app.manager import TaskManager

    monkeypatch.setenv("VIORUS_DATA_DIR", str(tmp_path))
    connector_port = free_port()
    task_runner_port = free_port()
    monkeypatch.setenv("VIORUS_RESERVATIONS_URL", f"http://127.0.0.1:{connector_port}")

    mock = MockReservationsTransport()
    res_api._vault = None
    res_api._connector = None
    res_api.get_connector = lambda: ReservationsConnector(res_api.get_vault(), transport=mock)

    task_api._manager = TaskManager(tmp_path / "task_runner.db")

    PermissionEngine.reset()
    register_phase6_connector_tools()

    services = {
        "connector": Server(res_api.app, connector_port, probe_path="/health"),
        "task_runner": Server(task_api.app, task_runner_port, probe_path="/tasks"),
    }
    for svc in services.values():
        svc.start()

    connector_url = f"http://127.0.0.1:{connector_port}"
    _pair(connector_url, mock)
    yield {
        "connector": connector_url,
        "task_runner": f"http://127.0.0.1:{task_runner_port}",
        "mock": mock,
    }


def test_observe_search_runs_without_approval(stack) -> None:
    base = stack["task_runner"]
    tid = _create_task(
        base,
        "reservations.search_slots",
        {"venue": "Trattoria Roma", "date": "2026-09-01", "party_size": 2},
        "observe",
        "find a table for two at Trattoria Roma",
    )
    assert requests.post(f"{base}/tasks/{tid}/start").status_code == 200

    r = requests.post(f"{base}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    step = r.json()["step"]
    assert step["ok"] is True
    assert r.json()["task"]["status"] == "completed"
    assert stack["mock"].bookings == []  # a search never books anything


def test_create_full_loop_pauses_approve_fires_once(stack) -> None:
    base = stack["task_runner"]
    tid = _create_task(
        base,
        "reservations.create",
        {
            "slot_id": "slot-1",
            "venue": "Trattoria Roma",
            "at": "2026-09-01T19:00:00",
            "party_size": 2,
            "guest_name": "Me",
        },
        "execute",
        "book a table for two at Trattoria Roma on the first of September",
    )
    assert requests.post(f"{base}/tasks/{tid}/start").status_code == 200

    # run -> pauses at the approval gate; nothing booked
    r = requests.post(f"{base}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "waiting_approval"
    assert stack["mock"].bookings == []

    # the diff card shows the venue, time, party, and guest
    approvals = requests.get(f"{base}/tasks/{tid}/approvals").json()
    assert len(approvals) == 1
    card = approvals[0]["diff_card"]
    assert card.get("venue") == "Trattoria Roma"
    assert card.get("guest_name") == "Me"

    # approve -> the generic executor dispatches to the connector, which
    # re-verifies the ledger record and books; task completes
    apr = requests.post(f"{base}/approvals/{approvals[0]['approval_id']}/approve")
    assert apr.status_code == 200, apr.text
    assert apr.json()["status"] == "completed", apr.json()

    # the booking reached the provider exactly once
    assert stack["mock"].bookings == [
        {"reservation_id": "res_1", "slot_id": "slot-1", "guest_name": "Me"}
    ]


def test_create_never_fires_without_approval(stack) -> None:
    base = stack["task_runner"]
    tid = _create_task(
        base,
        "reservations.create",
        {
            "slot_id": "slot-3",
            "venue": "Trattoria Roma",
            "at": "2026-09-01T21:00:00",
            "guest_name": "Me",
        },
        "execute",
        "book a later table",
    )
    assert requests.post(f"{base}/tasks/{tid}/start").status_code == 200
    r = requests.post(f"{base}/tasks/{tid}/run")
    assert r.json()["task"]["status"] == "waiting_approval"

    approval_id = requests.get(f"{base}/tasks/{tid}/approvals").json()[0]["approval_id"]
    rejected = requests.post(f"{base}/approvals/{approval_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "cancelled"
    assert stack["mock"].bookings == []  # rejection means it never books
