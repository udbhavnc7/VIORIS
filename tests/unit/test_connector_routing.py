"""Phase 6 connector routing tests: the task-runner executor dispatches every
registered connector tool to the right service over HTTP — Observe reads forward
only whitelisted query args, Execute writes forward the exact approval triple,
Prepare forwards body args with no triple, and anything else is refused.

No connector service is booted here: `httpx.Client` is stubbed with a recording
fake so we assert the exact HTTP call shape without touching the network.
"""

import pytest

from packages.shared.schemas import Task, TaskStep


class _FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {"echo": True}
        self.text = ""

    def json(self):
        return self._body


class _RecordingClient:
    """Records every get/post, returns a canned 200 JSON body."""

    def __init__(self, calls: list, status_code: int = 200) -> None:
        self._calls = calls
        self._status = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url: str, params: dict | None = None):
        self._calls.append(("GET", url, params, None))
        return _FakeResponse(self._status)

    def post(self, url: str, json: dict | None = None):
        self._calls.append(("POST", url, None, json))
        return _FakeResponse(self._status)


@pytest.fixture()
def recording(monkeypatch):
    """Point the executor's httpx at a recording fake client."""
    import services.task_runner.app.executor as executor_mod

    calls: list = []
    monkeypatch.setattr(
        executor_mod.httpx,
        "Client",
        lambda *a, **k: _RecordingClient(calls),
    )
    return calls


def _step(tool: str, args: dict, task=None) -> TaskStep:
    step = TaskStep(agent=tool.split(".")[0], tool=tool, risk_level="observe")
    step.result = {"args": args}
    return step


def _task(task_id: str = "task_x") -> Task:
    return Task(task_id=task_id, request="test", risk_level="observe")


def _run(tool: str, args: dict, task: Task | None = None):
    from services.task_runner.app.executor import make_executor

    return make_executor()(_step(tool, args, task), task=task)


def _assert_get(calls, base: str, path: str, params: dict | None = None) -> None:
    assert len(calls) == 1
    method, url, query, _ = calls[0]
    assert method == "GET"
    assert url == f"{base}{path}"
    assert query == (params or {})


def _assert_post(calls, base: str, path: str, body: dict) -> None:
    assert len(calls) == 1
    method, url, _, payload = calls[0]
    assert method == "POST"
    assert url == f"{base}{path}"
    for key, value in body.items():
        assert payload[key] == value, f"missing/wrong body key {key}"


class TestObserveReads:
    def test_gmail_digest_forwards_only_whitelisted_args(self, recording):
        _run("gmail.read_unread", {"hours": 12, "max_results": 5, "account": "me@x", "evil": 1})
        _assert_get(
            recording,
            "http://127.0.0.1:8445",
            "/digest",
            {"hours": 12, "max_results": 5, "account": "me@x"},
        )

    def test_gmail_digest_status_lists_accounts(self, recording):
        _run("gmail.digest_status", {})
        _assert_get(recording, "http://127.0.0.1:8445", "/accounts")

    def test_whatsapp_digest_uses_max_messages(self, recording):
        _run("whatsapp.read_digest", {"hours": 48, "max_messages": 7, "session_ref": "wa-1"})
        _assert_get(
            recording,
            "http://127.0.0.1:8446",
            "/digest",
            {"hours": 48, "max_messages": 7, "session_ref": "wa-1"},
        )

    def test_whatsapp_session_status(self, recording):
        _run("whatsapp.session_status", {})
        _assert_get(recording, "http://127.0.0.1:8446", "/sessions")

    def test_calendar_upcoming(self, recording):
        _run("calendar.read_upcoming", {"hours": 24})
        _assert_get(recording, "http://127.0.0.1:8447", "/digest", {"hours": 24})

    def test_cloud_files_recent(self, recording):
        _run("cloud_files.list_recent", {"hours": 6})
        _assert_get(recording, "http://127.0.0.1:8448", "/digest", {"hours": 6})

    def test_notes_recent(self, recording):
        _run("notes.list_recent", {"hours": 168})
        _assert_get(recording, "http://127.0.0.1:8449", "/digest", {"hours": 168})

    def test_contacts_search(self, recording):
        _run("contacts.search", {"query": "mom", "max_results": 3})
        _assert_get(
            recording, "http://127.0.0.1:8450", "/search", {"query": "mom", "max_results": 3}
        )

    def test_contacts_status_lists_accounts(self, recording):
        _run("contacts.digest_status", {})
        _assert_get(recording, "http://127.0.0.1:8450", "/accounts")

    def test_bookmarks_search_uses_source(self, recording):
        _run("bookmarks.search", {"query": "recipes", "source": "local"})
        _assert_get(
            recording,
            "http://127.0.0.1:8451",
            "/search",
            {"query": "recipes", "source": "local"},
        )

    def test_bookmarks_status_lists_sources(self, recording):
        _run("bookmarks.digest_status", {})
        _assert_get(recording, "http://127.0.0.1:8451", "/sources")

    def test_history_recent_uses_source(self, recording):
        _run("history.recent", {"hours": 12, "source": "chrome"})
        _assert_get(
            recording, "http://127.0.0.1:8452", "/recent", {"hours": 12, "source": "chrome"}
        )

    def test_history_status_lists_sources(self, recording):
        _run("history.digest_status", {})
        _assert_get(recording, "http://127.0.0.1:8452", "/sources")

    def test_reservations_search_requires_venue_forwarded(self, recording):
        _run(
            "reservations.search_slots",
            {"venue": "Trattoria", "date": "2026-08-20", "party_size": 4},
        )
        _assert_get(
            recording,
            "http://127.0.0.1:8454",
            "/search",
            {"venue": "Trattoria", "date": "2026-08-20", "party_size": 4},
        )


class TestExecuteWrites:
    def test_whatsapp_send_forwards_approval_triple(self, recording):
        task = _task()
        step = _step("whatsapp.send_message", {"recipient": "mom", "content": "hi"}, task)
        from services.task_runner.app.executor import make_executor

        make_executor()(step, task=task)
        _assert_post(
            recording,
            "http://127.0.0.1:8446",
            "/send",
            {
                "recipient": "mom",
                "content": "hi",
                "idempotency_key": step.idempotency_key,
                "task_id": "task_x",
                "step_id": step.step_id,
            },
        )

    def test_telephony_start_call_forwards_approval_triple(self, recording):
        task = _task()
        step = _step("telephony.start_call", {"recipient": "doc"}, task)
        from services.task_runner.app.executor import make_executor

        make_executor()(step, task=task)
        _assert_post(
            recording,
            "http://127.0.0.1:8453",
            "/start",
            {
                "recipient": "doc",
                "idempotency_key": step.idempotency_key,
                "task_id": "task_x",
                "step_id": step.step_id,
            },
        )

    def test_reservations_create_forwards_approval_triple(self, recording):
        task = _task()
        step = _step(
            "reservations.create",
            {"slot_id": "s1", "venue": "Trattoria", "at": "2026-08-20T19:00", "guest_name": "Me"},
            task,
        )
        from services.task_runner.app.executor import make_executor

        make_executor()(step, task=task)
        _assert_post(
            recording,
            "http://127.0.0.1:8454",
            "/create",
            {
                "slot_id": "s1",
                "venue": "Trattoria",
                "at": "2026-08-20T19:00",
                "guest_name": "Me",
                "idempotency_key": step.idempotency_key,
                "task_id": "task_x",
                "step_id": step.step_id,
            },
        )

    def test_write_drops_args_not_in_whitelist(self, recording):
        task = _task()
        step = _step("whatsapp.send_message", {"recipient": "mom", "content": "hi", "hax": 9}, task)
        from services.task_runner.app.executor import make_executor

        make_executor()(step, task=task)
        _, _, _, payload = recording[0]
        assert "hax" not in payload


class TestPrepare:
    def test_prepare_call_posts_no_approval_triple(self, recording):
        _run("telephony.prepare_call", {"recipient": "vet", "purpose": "appointment"})
        _assert_post(
            recording,
            "http://127.0.0.1:8453",
            "/prepare",
            {"recipient": "vet", "purpose": "appointment"},
        )
        _, _, _, payload = recording[0]
        assert "idempotency_key" not in payload
        assert "task_id" not in payload
        assert "step_id" not in payload


class TestUnknownTools:
    def test_unknown_connector_tool_refused(self):
        from services.task_runner.app.executor import make_executor

        with pytest.raises(RuntimeError):
            make_executor()(_step("whatsapp.send_money", {"amount": 100}))

    def test_unregistered_tool_refused(self):
        from services.task_runner.app.executor import make_executor

        with pytest.raises(RuntimeError):
            make_executor()(_step("calendar.delete_all", {}))
