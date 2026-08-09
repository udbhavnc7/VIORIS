from pathlib import Path

import pytest

from apps.desktop_agent.app.command_runner import run_command
from apps.desktop_agent.app.executor import Executor
from apps.desktop_agent.app.store import TaskStore

from packages.shared.permission_engine import PermissionEngine, register_phase1_tools

register_phase1_tools()


@pytest.fixture(autouse=True)
def phase1_registry():
    """Re-register Phase 1 tools: test_permission_engine's autouse fixture
    resets the registry, which wipes them when this module later in the suite."""
    PermissionEngine.reset()
    register_phase1_tools()
    yield


def make_store(tmp_path: str) -> TaskStore:
    return TaskStore(Path(tmp_path) / "vioris-test.db")


class TestRunner:
    def test_observe_command_runs_and_records(self, tmp_path):
        opened = []
        store = make_store(tmp_path)
        ex = Executor(open_app_fn=opened.append)
        out = run_command("open vs code", ex, store)

        assert out["tool"] == "system.open_app"
        assert out["status"] == "completed"
        assert out["blocked"] is None
        assert opened == ["vs code"]

        tasks = store.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["request"] == "open vs code"
        assert tasks[0]["status"] == "completed"

        ok, problems = store.verify_chain()
        assert ok, problems
        assert store.audit_events_copy()

    def test_unknown_tool_is_blocked_and_recorded(self, tmp_path):
        from apps.desktop_agent.app.executor import PermissionBlockedError

        store = make_store(tmp_path)

        class RejectingExecutor(Executor):
            def execute(self, command_result):
                raise PermissionBlockedError(f"Tool '{command_result.tool}' requires approval")

        out = run_command("what time is it", RejectingExecutor(), store)
        assert out["status"] == "waiting_approval"
        assert out["blocked"] is not None

        tasks = store.list_tasks()
        assert tasks[0]["status"] == "waiting_approval"

    def test_unknown_phrase_is_grateful_not_blocked(self, tmp_path):
        store = make_store(tmp_path)
        out = run_command("sing me a song", Executor(), store)
        assert out["tool"] == "system.unknown"
        assert out["status"] == "completed"
        assert "didn't catch" in out["reply"]


from fastapi.testclient import TestClient  # noqa: E402

from apps.desktop_agent.app.web import app as web_app  # noqa: E402


class TestWebConsole:
    def test_index_serves_html(self):
        client = TestClient(web_app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Vioris" in resp.text
        assert "console" in resp.text.lower()

    def test_command_endpoint_runs_through_pipeline(self):
        client = TestClient(web_app)
        resp = client.post("/api/command", json={"transcript": "what time is it"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["tool"] == "system.get_time"
        assert body["blocked"] is None
        assert "time" in body["reply"].lower()

    def test_tools_endpoint(self):
        client = TestClient(web_app)
        body = client.get("/api/tools").json()
        names = {t["tool_name"] for t in body}
        assert "system.get_time" in names
        assert {t["tier"] for t in body} <= {"observe", "prepare", "execute", "critical"}

    def test_tasks_and_audit_endpoints(self, tmp_path):
        store = TaskStore(Path(tmp_path) / "web-test.db")
        run_command("tell me the time", Executor(), store)

        client = TestClient(web_app)
        assert client.get("/api/tasks").status_code == 200
        audit = client.get("/api/audit").json()
        assert audit["ok"] is True
