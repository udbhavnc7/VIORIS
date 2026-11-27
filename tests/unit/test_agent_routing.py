"""Routing from the task-runner executor to the isolated local agents.

The browser and documents daemons are never booted here: `httpx.Client` is
stubbed with a recording fake so we assert the exact HTTP verbs, paths and
payloads. A daemon that reports ok:false inside a 200 must surface as a failure,
never as a fabricated success.
"""

from __future__ import annotations

import pytest

from packages.shared.schemas import TaskStep


BROWSER = "http://127.0.0.1:8431"
DOCUMENTS = "http://127.0.0.1:8432"
COMPUTER = "http://127.0.0.1:8430"


class _Response:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True, "service": "fake"}
        self.text = ""

    def json(self):
        return self._body


class _RecordingClient:
    def __init__(self, calls, status_code=200, body=None, raise_error=None):
        self.calls = calls
        self.status_code = status_code
        self.body = body
        self.raise_error = raise_error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, None))
        if self.raise_error:
            raise self.raise_error
        return _Response(self.status_code, self.body)

    def post(self, url, json=None, **kwargs):
        self.calls.append(("POST", url, json))
        if self.raise_error:
            raise self.raise_error
        return _Response(self.status_code, self.body)


@pytest.fixture
def recording(monkeypatch):
    import services.task_runner.app.executor as executor_mod

    calls = []
    holder = {"status": 200, "body": None, "error": None}
    monkeypatch.setattr(
        executor_mod.httpx,
        "Client",
        lambda *a, **k: _RecordingClient(
            calls, holder["status"], holder["body"], holder["error"]
        ),
    )
    holder["calls"] = calls
    return holder


def _step(tool, args):
    step = TaskStep(agent=tool.split(".")[0], tool=tool, risk_level="observe")
    step.result = {"args": args}
    return step


def _run(tool, args):
    from services.task_runner.app.executor import make_executor

    return make_executor(
        computer_url=COMPUTER, browser_url=BROWSER, documents_url=DOCUMENTS
    )(_step(tool, args))


@pytest.mark.parametrize(
    "tool,args,method,path,payload",
    [
        ("browser.navigate", {"url": "https://example.com"}, "POST", "/navigate", {"url": "https://example.com"}),
        ("browser.read_page", {}, "GET", "/page", None),
        ("browser.fill_field", {"description": "search box", "value": "python"}, "POST", "/fill", {"description": "search box", "value": "python"}),
        ("browser.click_element", {"element": "login", "description": "Login button"}, "POST", "/click", {"description": "Login button"}),
        ("file.search_files", {"query": "budget", "root": "notes"}, "POST", "/search", {"query": "budget", "root": "notes"}),
        ("file.read", {"path": "notes/a.md"}, "POST", "/read", {"path": "notes/a.md"}),
        ("file.move", {"source": "a.md", "destination": "b.md"}, "POST", "/move", {"source": "a.md", "destination": "b.md"}),
        ("file.rename", {"path": "a.md", "new_name": "b.md"}, "POST", "/rename", {"path": "a.md", "new_name": "b.md"}),
        ("file.delete", {"path": "a.md"}, "POST", "/delete", {"path": "a.md"}),
        ("terminal.run", {"command": "git", "args": ["status"]}, "POST", "/terminal", {"command": "git", "args": ["status"]}),
        ("computer.read_screen_text", {}, "GET", "/screen-text", None),
        ("computer.describe_screen", {"question": "What does this error say?"}, "POST", "/screen-describe", {"question": "What does this error say?"}),
        ("computer.describe_screen", {}, "POST", "/screen-describe", {"question": None}),
        ("computer.list_windows", {}, "GET", "/windows", None),
        ("computer.open_app", {"name": "notepad"}, "POST", "/open-app", {"name": "notepad"}),
        ("computer.lock_workstation", {"device_id": "phone1"}, "POST", "/remote/lock", {"device_id": "phone1"}),
    ],
)
def test_agent_tools_reach_the_right_daemon(recording, tool, args, method, path, payload):
    base = BROWSER if tool.startswith("browser.") else DOCUMENTS if tool.startswith(("file.", "terminal.")) else COMPUTER
    result = _run(tool, args)
    assert result["ok"] is True
    assert result["tool"] == tool
    assert recording["calls"] == [(method, f"{base}{path}", payload)]


def test_login_wall_is_reported_not_silently_ignored(recording):
    recording["body"] = {"ok": False, "note": "login wall — stopped at the browser agent"}
    with pytest.raises(RuntimeError, match="login wall"):
        _run("browser.navigate", {"url": "https://mail.example.com"})


def test_http_error_is_reported(recording):
    recording["status"] = 403
    recording["body"] = {"detail": "path outside allow-list"}
    with pytest.raises(RuntimeError, match="path outside allow-list"):
        _run("file.read", {"path": "../secrets.txt"})


def test_unreachable_agent_raises_instead_of_claiming_success(recording):
    import httpx

    recording["error"] = httpx.ConnectError("connection refused")
    with pytest.raises(RuntimeError, match="browser agent unreachable"):
        _run("browser.read_page", {})
