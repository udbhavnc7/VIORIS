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

REACT = {
    "type": "react",
    "reaction": "acknowledge",
    "target": "Disha",
    "message_id": "msg-123",
    "status": "not_executed",
    "description": "acknowledge on Disha's mail",
}

REPLY = {
    "type": "reply",
    "thread_id": "thread-9",
    "message_id": "msg-123",
    "to": "disha@example.com",
    "subject": "Re: Dinner",
    "body": "running late",
    "status": "not_sent",
    "description": "reply to Disha: 'running late' — not sent",
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

    def test_react_maps_to_registered_execute_tool(self):
        steps = gw._action_to_steps(REACT)
        assert steps == [
            {
                "agent": "gmail",
                "tool": "gmail.react",
                "args": {"message_id": "msg-123", "reaction": "acknowledge"},
                "reasoning": steps[0]["reasoning"],
            }
        ]
        assert "approval" in steps[0]["reasoning"]

    def test_reply_maps_to_registered_execute_tool(self):
        steps = gw._action_to_steps(REPLY)
        assert steps == [
            {
                "agent": "gmail",
                "tool": "gmail.send_reply",
                "args": {
                    "thread_id": "thread-9",
                    "message_id": "msg-123",
                    "to": "disha@example.com",
                    "subject": "Re: Dinner",
                    "body": "running late",
                },
                "reasoning": steps[0]["reasoning"],
            }
        ]
        assert "Execute-tier" in steps[0]["reasoning"]

    @pytest.mark.parametrize(
        "action",
        [
            {"type": "continue_task", "task": "deploy"},
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
            {"type": "react", "reaction": "acknowledge"},
            {"type": "react", "message_id": "msg-123", "reaction": "  "},
            {"type": "reply", "to": "d@example.com", "subject": "Re: x", "body": "hi"},
            {"type": "reply", "thread_id": "t", "subject": "Re: x", "body": "hi"},
            {"type": "reply", "thread_id": "t", "to": "d@example.com", "body": "hi"},
            {"type": "reply", "thread_id": "t", "to": "d@example.com", "subject": "Re: x"},
        ],
    )
    def test_incomplete_actions_are_refused(self, action):
        assert gw._action_to_steps(action) is None


class TestQueueThroughApprovalGate:
    @pytest.mark.asyncio
    async def test_message_creates_task_and_stops_at_gate(self, runner):
        runner.responses += [
            (200, {"task_id": "t-42", "status": "created"}),
            (200, {"task_id": "t-42", "status": "running"}),
            (200, {"task": {"task_id": "t-42", "status": "waiting_approval"}}),
            (200, [{"approval_id": "a-9", "task_id": "t-42", "tool": "system.send_message"}]),
        ]
        outcome = await gw._execute_call_action("call-1", MESSAGE)

        assert outcome["queued"] is True
        assert outcome["task_id"] == "t-42"
        assert outcome["approval_id"] == "a-9"
        assert outcome["status"] == "waiting_approval"
        assert "waiting for your approval" in outcome["note"]
        assert "sent" not in outcome["note"].lower().replace("anything is sent", "")

        assert [call[:2] for call in runner.calls] == [
            ("POST", "http://127.0.0.1:8421/tasks"),
            ("POST", "http://127.0.0.1:8421/tasks/t-42/start"),
            ("POST", "http://127.0.0.1:8421/tasks/t-42/run"),
            ("GET", "http://127.0.0.1:8421/approvals"),
        ]
        created = runner.calls[0][2]
        assert created["steps"][0]["tool"] == "system.send_message"
        assert created["steps"][0]["args"]["recipient"] == "Disha"
        # No approve/reject call was made anywhere.
        assert all("approve" not in call[1] for call in runner.calls)

    @pytest.mark.asyncio
    async def test_queued_run_polls_status_instead_of_enqueueing_again(self, runner):
        runner.responses += [
            (200, {"task_id": "t-44"}),
            (200, {"ok": True}),
            (
                200,
                {
                    "queued": True,
                    "task": {"task_id": "t-44", "status": "running", "steps": []},
                },
            ),
            (200, {"task_id": "t-44", "status": "waiting_approval", "steps": []}),
            (200, [{"approval_id": "a-10", "task_id": "t-44", "tool": "system.send_message"}]),
        ]
        outcome = await gw._execute_call_action("call-7", MESSAGE)

        assert outcome["queued"] is True
        assert outcome["approval_id"] == "a-10"
        assert outcome["status"] == "waiting_approval"
        assert [call[:2] for call in runner.calls] == [
            ("POST", "http://127.0.0.1:8421/tasks"),
            ("POST", "http://127.0.0.1:8421/tasks/t-44/start"),
            ("POST", "http://127.0.0.1:8421/tasks/t-44/run"),
            ("GET", "http://127.0.0.1:8421/tasks/t-44"),
            ("GET", "http://127.0.0.1:8421/approvals"),
        ]

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
    async def test_send_mail_refusal_names_the_missing_tool(self, runner):
        outcome = await gw._execute_call_action(
            "call-3", {"type": "send_mail", "recipient": "Disha", "email": "d@example.com"}
        )
        assert outcome["queued"] is False
        assert "reply inside an existing thread" in outcome["note"]
        assert "nothing was sent" in outcome["note"]
        assert runner.calls == []

    @pytest.mark.asyncio
    async def test_react_creates_task_and_stops_at_gate(self, runner):
        runner.responses += [
            (200, {"task_id": "t-44"}),
            (200, {"task_id": "t-44"}),
            (200, {"task": {"task_id": "t-44", "status": "waiting_approval"}}),
            (200, [{"approval_id": "a-10", "task_id": "t-44", "tool": "gmail.react"}]),
        ]
        outcome = await gw._execute_call_action("call-7", REACT)

        assert outcome["queued"] is True
        assert outcome["approval_id"] == "a-10"
        assert "waiting for your approval" in outcome["note"]
        assert "anything is applied" in outcome["note"]
        created = runner.calls[0][2]
        assert created["steps"][0]["tool"] == "gmail.react"
        assert created["steps"][0]["args"] == {
            "message_id": "msg-123",
            "reaction": "acknowledge",
        }
        assert all("approve" not in call[1] for call in runner.calls)

    @pytest.mark.asyncio
    async def test_reply_creates_task_and_stops_at_gate(self, runner):
        runner.responses += [
            (200, {"task_id": "t-45"}),
            (200, {"task_id": "t-45"}),
            (200, {"task": {"task_id": "t-45", "status": "waiting_approval"}}),
            (200, [{"approval_id": "a-11", "task_id": "t-45", "tool": "gmail.send_reply"}]),
        ]
        outcome = await gw._execute_call_action("call-8", REPLY)

        assert outcome["queued"] is True
        assert outcome["approval_id"] == "a-11"
        assert "waiting for your approval" in outcome["note"]
        assert "anything is sent" in outcome["note"]
        created = runner.calls[0][2]
        assert created["steps"][0]["tool"] == "gmail.send_reply"
        assert created["steps"][0]["args"]["thread_id"] == "thread-9"
        assert created["steps"][0]["args"]["body"] == "running late"
        assert all("approve" not in call[1] for call in runner.calls)

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


class _Session:
    def __init__(self, metadata):
        self.metadata = metadata


class _Pipeline:
    def __init__(self, session):
        self._session = session

    def get_session(self, call_id):
        return self._session


class TestVoiceApproval:
    @pytest.mark.asyncio
    async def test_approve_uses_the_tracked_approval_id(self, runner):
        session = _Session(
            {"pending_call_approval": {"task_id": "t-42", "approval_id": "a-9"}}
        )
        runner.responses.append((200, {"task_id": "t-42", "status": "completed"}))
        outcome = await gw._resolve_pending_call_action(
            "call-1", _Pipeline(session), approve=True
        )
        assert outcome["result"]["status"] == "completed"
        assert "Approved" in outcome["text"]
        assert runner.calls == [
            ("POST", "http://127.0.0.1:8421/approvals/a-9/approve", None)
        ]
        assert "pending_call_approval" not in session.metadata

    @pytest.mark.asyncio
    async def test_reject_uses_the_tracked_approval_id(self, runner):
        session = _Session(
            {"pending_call_approval": {"task_id": "t-42", "approval_id": "a-9"}}
        )
        runner.responses.append((200, {"rejected": "a-9", "status": "cancelled"}))
        outcome = await gw._resolve_pending_call_action(
            "call-1", _Pipeline(session), approve=False
        )
        assert "Rejected" in outcome["text"]
        assert runner.calls == [
            ("POST", "http://127.0.0.1:8421/approvals/a-9/reject", None)
        ]

    @pytest.mark.asyncio
    async def test_no_tracked_approval_falls_back_to_pipeline(self, runner):
        session = _Session({})
        outcome = await gw._resolve_pending_call_action(
            "call-1", _Pipeline(session), approve=True
        )
        assert outcome is None
        assert runner.calls == []

    @pytest.mark.asyncio
    async def test_second_factor_failure_is_reported_not_bypassed(self, runner):
        session = _Session(
            {"pending_call_approval": {"task_id": "t-42", "approval_id": "a-9"}}
        )
        runner.responses.append(
            (403, {"detail": "Critical action requires the second factor"})
        )
        outcome = await gw._resolve_pending_call_action(
            "call-1", _Pipeline(session), approve=True
        )
        assert "couldn't approve" in outcome["text"]
        assert "second factor" in outcome["text"]
        assert "pending_call_approval" not in session.metadata


def test_pipeline_is_wired_to_the_real_handler(monkeypatch):
    monkeypatch.setattr(gw, "_pipeline", None)
    pipeline = gw.get_pipeline()
    assert pipeline.on_action is gw._execute_call_action
    monkeypatch.setattr(gw, "_pipeline", None)


class _RecordingPipeline:
    def __init__(self, session):
        self._session = session
        self.utterances = []

    def get_session(self, call_id):
        return self._session

    async def handle_phone_message(self, call_id, text):
        self.utterances.append((call_id, text))
        return {"response_text": "pipeline handled", "action": None}


def _voice_handler(pipeline):
    from services.api.app.websocket import WebSocketHub

    hub = WebSocketHub()
    gw._setup_hub_handlers(hub, pipeline)
    return hub, hub._handlers["voice"][0]


def _voice_message(text, call_id="call-1"):
    from services.api.app.websocket import WSMessage

    return WSMessage(type="voice", payload={"call_id": call_id, "text": text})


class TestSpokenApproval:
    @pytest.mark.asyncio
    async def test_spoken_yes_resolves_the_tracked_approval(self, runner):
        session = _Session(
            {"pending_call_approval": {"task_id": "t-42", "approval_id": "a-9"}}
        )
        pipeline = _RecordingPipeline(session)
        hub, handler = _voice_handler(pipeline)
        runner.responses.append((200, {"task_id": "t-42", "status": "completed"}))

        await handler(_voice_message("yes"))

        assert runner.calls == [
            ("POST", "http://127.0.0.1:8421/approvals/a-9/approve", None)
        ]
        assert pipeline.utterances == []
        assert "pending_call_approval" not in session.metadata
        assert hub._message_queue.empty()  # nothing stray queued

    @pytest.mark.asyncio
    async def test_spoken_no_rejects_the_tracked_approval(self, runner):
        session = _Session(
            {"pending_call_approval": {"task_id": "t-42", "approval_id": "a-9"}}
        )
        pipeline = _RecordingPipeline(session)
        _hub, handler = _voice_handler(pipeline)
        runner.responses.append((200, {"rejected": "a-9", "status": "cancelled"}))

        await handler(_voice_message("no don't"))

        assert runner.calls == [
            ("POST", "http://127.0.0.1:8421/approvals/a-9/reject", None)
        ]
        assert pipeline.utterances == []

    @pytest.mark.asyncio
    async def test_prepared_action_still_owns_the_utterance(self, runner):
        session = _Session(
            {"pending_call_approval": {"task_id": "t-42", "approval_id": "a-9"}}
        )
        session.pending_actions = [{"type": "message", "contact": "Disha"}]
        pipeline = _RecordingPipeline(session)
        _hub, handler = _voice_handler(pipeline)

        await handler(_voice_message("yes"))

        assert runner.calls == []
        assert pipeline.utterances == [("call-1", "yes")]

    @pytest.mark.asyncio
    async def test_non_approval_utterance_goes_to_the_pipeline(self, runner):
        session = _Session(
            {"pending_call_approval": {"task_id": "t-42", "approval_id": "a-9"}}
        )
        pipeline = _RecordingPipeline(session)
        _hub, handler = _voice_handler(pipeline)

        await handler(_voice_message("what's in my inbox"))

        assert runner.calls == []
        assert pipeline.utterances == [("call-1", "what's in my inbox")]

