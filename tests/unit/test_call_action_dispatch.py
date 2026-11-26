"""Call-session hooks: confirmed actions must be auditable and honest.

A spoken "yes" may only produce an execute action; it must never imply that
anything was delivered when no handler is wired.
"""

from __future__ import annotations

import pytest

from packages.shared.contacts import Contact
from packages.shared.proactive_pipeline import (
    CallSession,
    DigestItem,
    ProactiveCallPipeline,
    RingSource,
)


class FakeHub:
    def __init__(self):
        self.messages = []

    async def send_to_phones(self, data):
        self.messages.append(data)
        return ["phone-1"]

    async def send_to_client(self, client_id, data):
        self.messages.append(data)
        return True

    def get_phone_clients(self):
        return []


class FakeGmail:
    def is_connected(self):
        return False

    def get_recent_messages(self, hours=24, max_results=5):
        return []


def _pipeline(**kwargs):
    return ProactiveCallPipeline(hub=FakeHub(), gmail=FakeGmail(), **kwargs)


def _session(pipeline, call_id="call-1"):
    session = CallSession(call_id=call_id, source=RingSource.PROACTIVE)
    session.pending_actions.append(
        {"type": "message", "contact": "Disha", "message": "hello", "description": "message to Disha"}
    )
    pipeline._sessions[call_id] = session
    return session


@pytest.mark.asyncio
async def test_confirmation_without_handler_does_not_claim_delivery():
    pipeline = _pipeline()
    _session(pipeline)
    result = await pipeline.handle_phone_message("call-1", "yes")
    assert result["action"]["type"] == "execute"
    assert result["queued"] is False
    assert "Nothing has been sent or executed" in result["response_text"]
    assert "Nothing has been sent or executed" in pipeline.hub.messages[-1]["payload"]["text"]


@pytest.mark.asyncio
async def test_sync_handler_reports_queueing_and_task_id():
    calls = []

    def handler(call_id, action):
        calls.append((call_id, action))
        return {"note": "Queued as task t-42.", "task_id": "t-42"}

    pipeline = _pipeline(on_action=handler)
    _session(pipeline)
    result = await pipeline.handle_phone_message("call-1", "yes")
    assert calls and calls[0][0] == "call-1"
    assert calls[0][1]["type"] == "message"
    assert result["queued"] is True
    assert result["task_id"] == "t-42"
    assert "Queued as task t-42." in result["response_text"]


@pytest.mark.asyncio
async def test_approval_id_is_tracked_for_voice_approval():
    def handler(call_id, action):
        return {
            "note": "Queued as task t-42; waiting for approval.",
            "task_id": "t-42",
            "approval_id": "a-9",
            "queued": True,
        }

    pipeline = _pipeline(on_action=handler)
    session = _session(pipeline)
    result = await pipeline.handle_phone_message("call-1", "yes")
    assert result["approval_id"] == "a-9"
    assert session.metadata["pending_call_approval"] == {
        "task_id": "t-42",
        "approval_id": "a-9",
    }


@pytest.mark.asyncio
async def test_async_handler_is_awaited():
    async def handler(call_id, action):
        return {"note": "Queued asynchronously."}

    pipeline = _pipeline(on_action=handler)
    _session(pipeline)
    result = await pipeline.handle_phone_message("call-1", "yes")
    assert "Queued asynchronously." in result["response_text"]


@pytest.mark.asyncio
async def test_handler_failure_is_surfaced_not_silently_ignored():
    def handler(call_id, action):
        raise RuntimeError("transport offline")

    pipeline = _pipeline(on_action=handler)
    _session(pipeline)
    result = await pipeline.handle_phone_message("call-1", "yes")
    assert "Could not queue that step: transport offline" in result["response_text"]


@pytest.mark.asyncio
async def test_digest_fetcher_is_used_as_a_connector_source():
    fetched = [DigestItem(item_type="task", summary="pipeline item", source="custom")]

    pipeline = _pipeline(digest_fetcher=lambda: fetched)
    pipeline._schedule.trigger_hours = [99]  # never triggers on the clock
    session = await pipeline._start_call("call-2", driving_scenario=False)
    assert any(item.summary == "pipeline item" for item in session.digest.items)


@pytest.mark.asyncio
async def test_async_digest_fetcher_is_awaited():
    async def fetch():
        return [DigestItem(item_type="task", summary="async item", source="custom")]

    pipeline = _pipeline(digest_fetcher=fetch)
    session = await pipeline._start_call("call-3", driving_scenario=False)
    assert any(item.summary == "async item" for item in session.digest.items)


@pytest.mark.asyncio
async def test_contact_loader_is_used_when_contact_book_is_empty():
    pipeline = _pipeline(
        contact_loader=lambda: [Contact(name="Loaded Contact", email="loaded@example.com")]
    )
    pipeline._schedule.trigger_hours = [99]
    await pipeline._start_call("call-4", driving_scenario=False)
    assert [c.name for c in pipeline.contacts.list_all()] == ["Loaded Contact"]


@pytest.mark.asyncio
async def test_failing_fetcher_does_not_block_a_call():
    def broken():
        raise RuntimeError("connector offline")

    pipeline = _pipeline(digest_fetcher=broken)
    session = await pipeline._start_call("call-5", driving_scenario=False)
    assert session is not None
    assert session.call_id == "call-5"
