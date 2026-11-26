"""Confirmed call actions queue through the task runner's approval gate.

No network: the loopback task-runner client is stubbed. The critical assertion
is that a spoken confirmation creates a task and STOPS at the gate — it never
auto-approves or claims delivery.
"""

from __future__ import annotations

import pytest

from services.api_gateway.app import main as gw


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _AsyncClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url, json))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(*item)


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setattr(gw, "TASK_RUNNER_URL", "http://127.0.0.1:8421")
    client = _AsyncClient([])
    monkeypatch.setattr(gw, "_client", lambda: client)
    return client


MESSAGE = {
    "type": "message",
    "contact": "Disha",
    "message": "running late",
    "description": "message to Disha",
}


class TestActionMapping:
    def test_message_maps_to_registered_execute_tool(self):
        steps = gw._action_to_steps(MESSAGE)
        assert steps == [
            {
                "agent": "system",
                "tool": "system.send_message",
                "args": {"recipient": "Disha", "content": "running late", "channel": "whatsapp"},
                "reasoning": steps[0]["reasoning"],
            }
        ]
        assert "Execute-tier" in steps[0]["reasoning"]

    @pytest.mark.parametrize(
        "action",
        [
            {"type": "continue_task", "task": "deploy"},
            {"type": "react", "reaction": "👍"},
            {"type": "send_mail", "recipient": "x"},
            {"type": "open_app", "target": "gmail"},
            {},
        ],
    )
    def test_unmapped_actions_are_refused(self, action):
        assert gw._action_to_steps(action) is None

    @pytest.mark.parametrize(
        "action",
        [
            {"type": "message", "contact": "", "message": "hi"},
            {"type": "message", "contact": "Disha", "message": "  "},
        ],
    )
    def test_incomplete_message_is_refused(self, action):
        assert gw._action_to_steps(action) is None


class TestQueueThroughApprovalGate:
    @pytest.mark.asyncio
    async def test_message_creates_task_and_stops_at_gate(self, runner):
        runner.responses += [
            (200, {"task_id": "t-42", "status": "created"}),
            (200, {"task_id": "t-42", "status": "running"}),
            (200, {"task": {"task_id": "t-42", "status": "waiting_approval"}}),
        ]
        outcome = await gw._execute_call_action("call-1", MESSAGE)

        assert outcome["queued"] is True
        assert outcome["task_id"] == "t-42"
        assert outcome["status"] == "waiting_approval"
        assert "waiting for your approval" in outcome["note"]
        assert "sent" not in outcome["note"].lower().replace("anything is sent", "")

        assert [call[:2] for call in runner.calls] == [
            ("POST", "http://127.0.0.1:8421/tasks"),
            ("POST", "http://127.0.0.1:8421/tasks/t-42/start"),
            ("POST", "http://127.0.0.1:8421/tasks/t-42/run"),
        ]
        created = runner.calls[0][2]
        assert created["steps"][0]["tool"] == "system.send_message"
        assert created["steps"][0]["args"]["recipient"] == "Disha"
        # No approve/reject call was made anywhere.
        assert all("approve" not in call[1] for call in runner.calls)

    @pytest.mark.asyncio
    async def test_completed_task_reports_completion(self, runner):
        runner.responses += [
            (200, {"task_id": "t-43"}),
            (200, {"task_id": "t-43"}),
            (200, {"task": {"task_id": "t-43", "status": "completed"}}),
        ]
        outcome = await gw._execute_call_action("call-2", MESSAGE)
        assert outcome["queued"] is True
        assert "completed" in outcome["note"]

    @pytest.mark.asyncio
    async def test_unsupported_action_does_not_touch_the_runner(self, runner):
        outcome = await gw._execute_call_action("call-3", {"type": "continue_task"})
        assert outcome["queued"] is False
        assert "nothing was executed" in outcome["note"]
        assert runner.calls == []

    @pytest.mark.asyncio
    async def test_runner_rejection_is_surfaced(self, runner):
        runner.responses.append((422, {"detail": "unknown tool"}))
        outcome = await gw._execute_call_action("call-4", MESSAGE)
        assert outcome["queued"] is False
        assert "Could not queue that step: unknown tool" in outcome["note"]

    @pytest.mark.asyncio
    async def test_unreachable_runner_is_surfaced(self, runner):
        import httpx

        runner.responses.append(httpx.ConnectError("refused"))
        outcome = await gw._execute_call_action("call-5", MESSAGE)
        assert outcome["queued"] is False
        assert "task-runner unreachable" in outcome["note"]

    @pytest.mark.asyncio
    async def test_missing_task_id_is_not_reported_as_queued(self, runner):
        runner.responses.append((200, {"status": "created"}))
        outcome = await gw._execute_call_action("call-6", MESSAGE)
        assert outcome["queued"] is False
        assert "nothing was executed" in outcome["note"]


def test_pipeline_is_wired_to_the_real_handler(monkeypatch):
    monkeypatch.setattr(gw, "_pipeline", None)
    pipeline = gw.get_pipeline()
    assert pipeline.on_action is gw._execute_call_action
    monkeypatch.setattr(gw, "_pipeline", None)
