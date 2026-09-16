"""Documents agent integration: daemon HTTP surface + permission gating."""

import pytest
from fastapi.testclient import TestClient

from agents.documents.app.agent import DocumentsAgent
from agents.documents.app.allowlist import GuardrailConfig
from agents.documents.app.daemon import app
from packages.shared.permission_engine import PermissionEngine, register_phase3_files_tools


@pytest.fixture(autouse=True)
def _registry():
    from agents.documents.app import daemon as docs_daemon

    PermissionEngine.reset()
    register_phase3_files_tools()
    yield
    docs_daemon._agent = None
    PermissionEngine.reset()


@pytest.fixture
def client(tmp_path_factory):
    from agents.documents.app import daemon as docs_daemon

    root = tmp_path_factory.mktemp("docs")
    (root / "notes.txt").write_text("vioris notes")
    docs_daemon._agent = DocumentsAgent(
        config=GuardrailConfig(
            allowed_dirs=(root.resolve(),),
            shell_programs=("python",),
        )
    )
    with TestClient(app) as c:
        c._root = root
        yield c


def test_search_and_read_inside_allowlist(client):
    r = client.post("/search", json={"query": "notes"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["detail"]["count"] == 1
    r = client.post("/read", json={"path": str(client._root / "notes.txt")})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "vioris" in r.json()["detail"]["preview"]


def test_operation_outside_allowlist_refused(client):
    r = client.post("/read", json={"path": str(client._root.parent / "outside.txt")})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["blocked"] == "outside-allowlist"
    r = client.post("/move", json={"source": str(client._root / "nope"), "destination": "x"})
    assert r.json()["blocked"] == "outside-allowlist"


def test_delete_is_critical_and_gated(client):
    from packages.shared.permission_engine import PermissionEngine

    assert PermissionEngine.classify("file.delete").requires_second_factor is True
    r = client.post("/delete", json={"path": str(client._root / "notes.txt")})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["detail"]["critical_gate_required"] is True


def test_terminal_refuses_non_allowlisted_command(client):
    r = client.post("/terminal", json={"command": "git", "args": ["status"]})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["blocked"] == "command-allowlist"


def test_terminal_allows_allowlisted_program(client):
    r = client.post("/terminal", json={"command": "python", "args": ["-c", "print('ok')"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["detail"]["returncode"] == 0


def test_engine_blocks_unregistered_phase3_file_surface():
    agent = DocumentsAgent()
    with pytest.raises(Exception) as ei:
        agent.classify_with_gate("file.steal_credentials")
    assert "not registered" in str(ei.value)