"""Tests for the proactive call pipeline."""

import asyncio
import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def send_text(self, text):
        self.sent.append(text)

    async def receive_text(self):
        await asyncio.Event().wait()

    async def close(self, code=None, reason=None):
        self.closed = True


class FakeHub:
    """Minimal hub that records sent messages."""

    def __init__(self):
        self.messages: list[dict] = []
        self._clients: dict = {}

    async def send_to_phones(self, data: dict) -> list[str]:
        self.messages.append(data)
        return ["phone-1"]

    async def send_to_client(self, client_id: str, data: dict) -> bool:
        self.messages.append(data)
        return True

    def get_phone_clients(self):
        return []


class FakeGmail:
    """Mock Gmail connector."""

    def __init__(self, connected=True, messages=None):
        self._connected = connected
        self._messages = messages or []

    def is_connected(self):
        return self._connected

    def list_messages(self, query="", max_results=10):
        return self._messages


# ── imports ──────────────────────────────────────────────────────────────────

from packages.shared.call_experience import RingSource
from packages.shared.contacts import Contact, ContactBook
from packages.shared.proactive_pipeline import (
    CallSession,
    CallRole,
    DigestStatus,
    DigestTurn,
    ProactiveCallPipeline,
)


# ── DigestSchedule ──────────────────────────────────────────────────────────


class TestDigestSchedule:
    def test_triggers_at_8am(self):
        from packages.shared.call_experience import DigestSchedule

        schedule = DigestSchedule()
        now = datetime(2026, 9, 17, 8, 30, tzinfo=UTC)
        assert schedule.should_trigger(now) is True

    def test_does_not_trigger_at_3am(self):
        from packages.shared.call_experience import DigestSchedule

        schedule = DigestSchedule()
        now = datetime(2026, 9, 17, 3, 0, tzinfo=UTC)
        assert schedule.should_trigger(now) is False

    def test_does_not_trigger_twice_in_hour(self):
        from packages.shared.call_experience import DigestSchedule

        schedule = DigestSchedule()
        now = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)
        assert schedule.should_trigger(now) is True
        schedule.record_trigger(now)
        now2 = datetime(2026, 9, 17, 8, 30, tzinfo=UTC)
        assert schedule.should_trigger(now2) is False

    def test_next_trigger(self):
        from packages.shared.call_experience import DigestSchedule

        schedule = DigestSchedule()
        now = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
        nxt = schedule.next_trigger(now)
        assert nxt is not None
        assert nxt.hour == 13


# ── ProactiveCallPipeline ───────────────────────────────────────────────────


class TestProactiveCallPipeline:
    @pytest.mark.asyncio
    async def test_tick_triggers_at_right_time(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))

        # Force trigger
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        pipeline._schedule.last_digest = None

        call_id = await pipeline.tick()
        assert call_id is not None
        assert len(hub.messages) == 1
        assert hub.messages[0]["type"] == "ring"

    @pytest.mark.asyncio
    async def test_tick_no_trigger_wrong_hour(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub)
        pipeline._schedule.trigger_hours = [0]  # unlikely hour

        call_id = await pipeline.tick()
        assert call_id is None

    @pytest.mark.asyncio
    async def test_handle_call_accept(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))

        # Start a call
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        # Accept the call
        spoken = await pipeline.handle_call_accept(call_id)
        assert spoken is not None
        assert len(spoken) > 0

    @pytest.mark.asyncio
    async def test_handle_phone_message_bye(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(call_id, "bye")
        assert result["digest_status"] == "completed"
        assert "later" in result["response_text"].lower()

    @pytest.mark.asyncio
    async def test_handle_phone_message_skip(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        # No items → skip ends the call
        result = await pipeline.handle_phone_message(call_id, "skip")
        assert result["digest_status"] == "completed"

    @pytest.mark.asyncio
    async def test_handle_phone_message_skip_with_items(self):
        from packages.shared.email_connector import EmailMessage

        messages = [
            EmailMessage(id="1", from_addr="A", subject="S1", is_read=False),
            EmailMessage(id="2", from_addr="B", subject="S2", is_read=False),
        ]
        hub = FakeHub()
        gmail = FakeGmail(connected=True, messages=messages)
        pipeline = ProactiveCallPipeline(hub=hub, gmail=gmail)
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(call_id, "skip")
        assert result["digest_status"] == "awaiting_response"
        assert "items left" in result["response_text"]

    @pytest.mark.asyncio
    async def test_handle_phone_message_tell_contact(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(
            call_id, "tell disha I'll be late"
        )
        assert "dish" in result["response_text"].lower()
        assert result["action"] is None  # waiting for approval

    @pytest.mark.asyncio
    async def test_handle_phone_message_approve(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        # First, set up a pending action
        session = pipeline.get_session(call_id)
        session.pending_actions.append(
            {"type": "message", "contact": "Disha", "message": "test"}
        )

        result = await pipeline.handle_phone_message(call_id, "yes")
        assert result["digest_status"] == "response_received"
        assert result["action"]["type"] == "execute"

    @pytest.mark.asyncio
    async def test_handle_phone_message_reject(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        session = pipeline.get_session(call_id)
        session.pending_actions.append(
            {"type": "message", "contact": "Disha", "message": "test"}
        )

        result = await pipeline.handle_phone_message(call_id, "no don't")
        assert "cancelled" in result["response_text"].lower()

    @pytest.mark.asyncio
    async def test_handle_phone_message_unknown_call(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub)
        result = await pipeline.handle_phone_message("nonexistent", "hello")
        assert result["response_text"] == "Call not found"

    @pytest.mark.asyncio
    async def test_handle_phone_message_emoji_reaction(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(call_id, "react with thumbs up")
        assert "👍" in result["response_text"]

    @pytest.mark.asyncio
    async def test_get_active_sessions(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]

        await pipeline.tick()
        active = pipeline.get_active_sessions()
        assert len(active) == 1

    @pytest.mark.asyncio
    async def test_fetch_items_from_gmail(self):
        from packages.shared.email_connector import EmailMessage

        messages = [
            EmailMessage(
                id="1",
                from_addr="Alice <alice@test.com>",
                subject="Test Subject",
                is_read=False,
            )
        ]
        hub = FakeHub()
        gmail = FakeGmail(connected=True, messages=messages)
        pipeline = ProactiveCallPipeline(hub=hub, gmail=gmail)
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]

        call_id = await pipeline.tick()
        session = pipeline.get_session(call_id)
        assert session.digest is not None
        assert len(session.digest.items) == 1
        assert session.digest.items[0].item_type == "email"
        assert "Alice" in session.digest.items[0].summary


# ── CallSession ──────────────────────────────────────────────────────────────


class TestCallSession:
    def test_add_turn(self):
        session = CallSession(call_id="c1", source=RingSource.PROACTIVE)
        turn = session.add_turn("phone", "hello")
        assert len(session.turns) == 1
        assert turn.role == "phone"
        assert turn.text == "hello"

    def test_get_user_intent(self):
        session = CallSession(call_id="c1", source=RingSource.PROACTIVE)
        session.add_turn("laptop", "digest text")
        session.add_turn("phone", "tell disha hi")
        assert session.get_user_intent() == "tell disha hi"

    def test_get_user_intent_empty(self):
        session = CallSession(call_id="c1", source=RingSource.PROACTIVE)
        assert session.get_user_intent() == ""


# ── DigestTurn ───────────────────────────────────────────────────────────────


class TestDigestTurn:
    def test_create(self):
        turn = DigestTurn(role="phone", text="hello")
        assert turn.role == "phone"
        assert turn.text == "hello"
        assert turn.timestamp > 0

    def test_with_metadata(self):
        turn = DigestTurn(role="laptop", text="digest", metadata={"type": "digest"})
        assert turn.metadata["type"] == "digest"
