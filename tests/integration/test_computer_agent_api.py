"""Computer agent integration tests: daemon HTTP surface + permission gating.

Includes the CONTRIBUTING requirement: prove the permission engine blocks an
action/surface that is not registered before anything runs.
"""


import pytest
from fastapi.testclient import TestClient

from agents.computer.app.agent import ComputerAgent, PermissionBlockedError
from agents.computer.app.daemon import app
from packages.shared.permission_engine import PermissionEngine, register_phase3_tools

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
    agent = ComputerAgent(screenshot_dir=tmp_path, list_windows_fn=list)
    with pytest.raises(PermissionBlockedError) as ei:
        agent.classify_with_gate("computer.burn_disk")
    assert "not registered" in str(ei.value)


def test_registered_computer_tools_are_observe_tier(client):
    from packages.shared.permission_engine import PermissionEngine

    for tool in ("computer.list_windows", "computer.open_app", "computer.capture_screenshot", "computer.read_screen_text"):
        assert PermissionEngine.classify(tool).confirmation_required is False


def test_screenshot_image_requires_active_session(client):
    r = client.get("/remote/screenshot-image?device_id=phone-1")
    assert r.status_code == 403  # no session -> no frame, ever


def test_screenshot_image_delivers_png_inside_session(client):
    r = client.post("/remote/session", json={"device_id": "phone-1", "timeout_minutes": 5})
    assert r.status_code == 200
    session = r.json()["session"]
    r = client.get("/remote/screenshot-image?device_id=phone-1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
    assert session["expired"] is False
    # after ending the session the same frame request is refused again
    client.post("/remote/session/end", json={"device_id": "phone-1"})
    assert client.get("/remote/screenshot-image?device_id=phone-1").status_code == 403


def test_remote_lock_requires_active_session(client):
    assert client.post("/remote/lock", json={"device_id": "phone-1"}).status_code == 403


def test_remote_lock_locks_then_ends_session(client, monkeypatch):
    locked = []
    from agents.computer.app import daemon as computer_daemon

    def fake_lock():
        locked.append(True)

    # Re-slot the agent so the injected lock backend is used and the lock is verifiable.
    computer_daemon._agent = ComputerAgent(
        screenshot_dir=None,
        list_windows_fn=list,
        screenshot_fn=(lambda p: (p.write_bytes(b"\x89PNG" + b"\x00" * 8), p)[1]),
        lock_workstation_fn=fake_lock,
    )
    client.post("/remote/session", json={"device_id": "phone-2", "timeout_minutes": 5})
    r = client.post("/remote/lock", json={"device_id": "phone-2"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert locked == [True]
    # session was ended by the lock — no further frames or locks allowed
    assert client.get("/remote/screenshot-image?device_id=phone-2").status_code == 403
    assert client.post("/remote/lock", json={"device_id": "phone-2"}).status_code == 403