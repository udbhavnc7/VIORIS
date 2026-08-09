"""Computer agent integration tests: daemon HTTP surface + permission gating.

Includes the CONTRIBUTING requirement: prove the permission engine blocks an
action/surface that is not registered before anything runs.
"""


import pytest
from fastapi.testclient import TestClient

from packages.shared.permission_engine import PermissionEngine, register_phase3_tools

from agents.computer.app.agent import ComputerAgent, PermissionBlockedError
from agents.computer.app.daemon import app

register_phase3_tools()


@pytest.fixture(autouse=True)
def _registry():
    from agents.computer.app import daemon as computer_daemon

    PermissionEngine.reset()
    register_phase3_tools()
    yield
    computer_daemon._agent = None
    PermissionEngine.reset()


@pytest.fixture
def client(tmp_path):
    from agents.computer.app import daemon as computer_daemon

    computer_daemon._agent = ComputerAgent(
        screenshot_dir=tmp_path,
        list_windows_fn=lambda: [
            {"handle": 1, "title": "Visual Studio Code — hello.py"},
        ],
        screenshot_fn=(lambda p: (p.parent.mkdir(parents=True, exist_ok=True), p.write_bytes(b"\x89PNG" + b"\x00" * 16), p)[2]),
        ocr_fn=lambda _p: "hello vioris\n",
        open_app_fn=lambda _n: None,
    )
    with TestClient(app) as c:
        yield c


def test_windows_endpoint(client):
    r = client.get("/windows")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["verified"] is True
    assert body["detail"]["count"] == 1


def test_open_app_endpoint_verifies_screen(client):

    r = client.post("/open-app", json={"name": "Visual Studio Code"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["verified"] is True  # screen re-check matched the title


def test_open_app_requires_name(client):
    r = client.post("/open-app", json={"name": ""})
    assert r.status_code == 422


def test_permission_engine_blocks_unregistered_computer_step(tmp_path):
    """CONTRIBUTING gate: an unregistered stop surface never fires."""
    agent = ComputerAgent(screenshot_dir=tmp_path, list_windows_fn=lambda: [])
    with pytest.raises(PermissionBlockedError) as ei:
        agent.classify_with_gate("computer.burn_disk")
    assert "not registered" in str(ei.value)


def test_registered_computer_tools_are_observe_tier(client):
    from packages.shared.permission_engine import PermissionEngine

    for tool in ("computer.list_windows", "computer.open_app", "computer.capture_screenshot", "computer.read_screen_text"):
        assert PermissionEngine.classify(tool).confirmation_required is False