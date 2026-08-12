"""Phase 5 full-stack exit test: real uvicorn servers, phone-driven.

Spins up the computer-agent daemon, task-runner, and api-gateway as real HTTP
services and drives the ENTIRE phone flow through the pair of gateways the
mobile app actually uses:

    pair → start remote session → the task pauses at the approval gate →
    approve from the phone → task-runner executor calls the computer agent →
    live session exists → phone pulls a frame and locks the laptop.

No component is stubbed: the executor performs real HTTP dispatch and the
diff-card/approval/audit path is the production one. The computer agent's OS
backends are injected (no live desktop needed), but every hop between the
services is real HTTP. This is the Prompt 5.x exit test ("approve from your
phone while the laptop is out of sight").
"""

from __future__ import annotations

import pytest
import requests

from tests.integration._server import Server as _Server, free_port


@pytest.fixture()
def full_stack(tmp_path, monkeypatch):
    """Boot the three services on free ports, wire by env var, inject a safe
    computer-agent OS backend so no live desktop is required."""
    computer_port = free_port()
    task_runner_port = free_port()
    gateway_port = free_port()

    from agents.computer.app import daemon as computer_daemon
    from agents.computer.app.agent import ComputerAgent
    from agents.computer.app.remote_control import RemoteControl

    shots = tmp_path / "shots"

    def fake_screenshot(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG" + b"\x00" * 32)
        return path

    computer_daemon._remote = RemoteControl()
    computer_daemon._agent = ComputerAgent(
        screenshot_dir=shots,
        list_windows_fn=lambda: [{"handle": 1, "title": "VS Code"}],
        screenshot_fn=fake_screenshot,
        ocr_fn=lambda _p: "live screen text\n",
        open_app_fn=lambda _n: None,
        lock_workstation_fn=lambda: None,
    )

    monkeypatch.setenv("VIORUS_COMPUTER_URL", f"http://127.0.0.1:{computer_port}")

    services = {
        "computer": _Server(computer_daemon.app, computer_port, probe_path="/windows"),
        "task_runner": _Server(_task_runner_app(tmp_path), task_runner_port, probe_path="/tasks"),
        "gateway": _Server(_gateway_app(monkeypatch, task_runner_port, computer_port), gateway_port),
    }
    for name, svc in services.items():
        svc.start()
    yield {"ports": services, "computer": computer_port, "gateway": gateway_port,
           "task_runner": task_runner_port}


def _task_runner_app(tmp_path):
    from services.task_runner.app import api as task_api
    from services.task_runner.app.manager import TaskManager

    task_api._manager = TaskManager(tmp_path / "task_runner.db")
    return task_api.app


def _gateway_app(monkeypatch, task_runner_port: int, computer_port: int):
    from services.api_gateway.app import main as gw
    from services.api_gateway.app.pairing import PairingStore

    gw._store = PairingStore(secret="test-secret")
    gw.TASK_RUNNER_URL = f"http://127.0.0.1:{task_runner_port}"
    gw.COMPUTER_URL = f"http://127.0.0.1:{computer_port}"
    return gw.app


def test_phone_remote_session_approve_screenshot_lock(full_stack):
    """The full 5.3 loop, driven exactly as the Flutter app does."""
    base = f"http://127.0.0.1:{full_stack['gateway']}"

    # 1. pair
    pair = requests.post(f"{base}/auth/device/pair", json={"name": "phone"}).json()
    ex = requests.post(
        f"{base}/auth/device/exchange",
        json={"device_id": pair["device_id"], "token": pair["pairing_token"]},
    ).json()
    jwt = ex["jwt"]
    h = {"Authorization": f"Bearer {jwt}"}

    # 2. request remote session
    start = requests.post(f"{base}/v1/remote/start", json={"timeout_minutes": 5}, headers=h)
    assert start.status_code == 200, start.text

    # 3. an approval is pending (diff card shows device)
    approvals = requests.get(f"{base}/v1/approvals", headers=h).json()["approvals"]
    assert len(approvals) == 1, approvals
    card = approvals[0]["diff_card"]
    assert card.get("device_id") == pair["device_id"]

    # 4. approve from the phone — the step auto-runs against the real computer
    #    agent daemon, so the laptop needs no further attention. Wait it out.
    apr = requests.post(f"{base}/v1/approvals/{approvals[0]['approval_id']}/approve", headers=h)
    assert apr.status_code == 200, apr.text
    assert apr.json()["result"]["status"] == "completed", apr.json()

    # 5. no pending approval remains; the task finished
    assert requests.get(f"{base}/v1/approvals", headers=h).json()["approvals"] == []

    # 6. a live session now exists for the device
    sess = requests.get(f"{base}/v1/remote/session", headers=h).json()["session"]
    assert sess is not None and sess["expired"] is False, sess

    # 7. on-demand frame is a PNG (throttled, session-gated)
    shot = requests.get(f"{base}/v1/remote/screenshot", headers=h)
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"

    # 8. lock from the phone locks + ends the session
    lock = requests.post(f"{base}/v1/remote/lock", headers=h)
    assert lock.status_code == 200 and lock.json()["ok"] is True, lock.text
    after = requests.get(f"{base}/v1/remote/session", headers=h).json()["session"]
    assert after is None, after