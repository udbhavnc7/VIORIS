"""Tests for the proactive call pipeline."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
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
from packages.shared.call_triggers import CallTriggerStore
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
        contacts = ContactBook(
            [Contact(name="Disha", phone="+919876500001", aliases=["wife"])]
        )
        pipeline = ProactiveCallPipeline(
            hub=hub, gmail=FakeGmail(connected=False), contacts=contacts
        )
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(
            call_id, "tell disha I'll be late"
        )
        assert "dish" in result["response_text"].lower()
        assert result["action"] is None  # waiting for approval

    @pytest.mark.asyncio
    async def test_handle_phone_message_tell_by_relationship(self):
        """'tell my wife...' resolves without an explicit alias (item 10c)."""
        contacts = ContactBook([Contact(name="Disha", relationship="wife")])
        pipeline = ProactiveCallPipeline(
            hub=FakeHub(), gmail=FakeGmail(connected=False), contacts=contacts
        )
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(
            call_id, "tell my wife I'll be late"
        )
        assert "Disha" in result["response_text"]
        assert "i'll be late" in result["response_text"].lower()
        pending = pipeline.get_session(call_id).pending_actions
        assert pending[0]["contact"] == "Disha"
        assert pending[0]["message"] == "i'll be late"

    @pytest.mark.asyncio
    async def test_handle_phone_message_tell_by_the_spouse(self):
        contacts = ContactBook([Contact(name="Disha", relationship="wife")])
        pipeline = ProactiveCallPipeline(
            hub=FakeHub(), gmail=FakeGmail(connected=False), contacts=contacts
        )
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(
            call_id, "tell the spouse to book a table"
        )
        pending = pipeline.get_session(call_id).pending_actions
        assert pending[0]["contact"] == "Disha"
        assert pending[0]["message"] == "book a table"
        assert "Disha" in result["response_text"]

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

    @pytest.mark.asyncio
    async def test_no_demo_contacts_or_script_by_default(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        assert pipeline.contacts.count() == 0

        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()
        session = pipeline.get_session(call_id)
        assert session.spoken_text == "Nothing new right now."
        assert "Chandragiri" not in session.spoken_text
        assert "LUME" not in session.spoken_text

    @pytest.mark.asyncio
    async def test_open_mail_uses_the_connected_mailbox(self):
        from packages.shared.email_connector import EmailMessage

        messages = [
            EmailMessage(
                id="m1",
                from_addr="Alice <alice@test.com>",
                subject="Budget review",
                is_read=False,
            )
        ]
        pipeline = ProactiveCallPipeline(
            hub=FakeHub(),
            gmail=FakeGmail(connected=True, messages=messages),
        )
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(call_id, "open mail")
        text = result["response_text"]
        assert "Alice" in text
        assert "Budget review" in text
        assert "RazorClub" not in text

    @pytest.mark.asyncio
    async def test_multi_intent_query_uses_the_connected_mailbox(self):
        from packages.shared.email_connector import EmailMessage

        messages = [
            EmailMessage(
                id="m1",
                from_addr="Alice <alice@test.com>",
                subject="Budget review",
                is_read=False,
            )
        ]
        pipeline = ProactiveCallPipeline(
            hub=FakeHub(),
            gmail=FakeGmail(connected=True, messages=messages),
        )
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()

        result = await pipeline.handle_phone_message(
            call_id, "what emails do I have, any texts?"
        )
        text = result["response_text"]
        assert "Alice" in text
        assert "Budget review" in text
        assert "RazorClub" not in text
        assert "Disha" not in text


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


# ── Driving Workflow E2E ─────────────────────────────────────────────────────


class TestDrivingWorkflow:
    @pytest.mark.asyncio
    async def test_driving_conversation_lifecycle(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        session = await pipeline._start_call("drive_01", driving_scenario=True)

        # 1. Spoken greeting is composed from the digest, not a canned script
        assert "LUME" in session.spoken_text
        assert "RazorClub" in session.spoken_text
        assert "Disha" in session.spoken_text
        assert "next phase" not in session.spoken_text
        # Demo contacts are seeded only for the explicit driving scenario.
        assert pipeline.contacts.count() == 3

        # 2. Turn 1: user confirms continuation and asks about mails & Disha
        turn1 = await pipeline.handle_phone_message(
            "drive_01",
            "Yes, go on, also tell me which mails have I recieved, are there any texts from Disha?",
        )
        t1_resp = turn1["response_text"]
        assert "RazorClub" in t1_resp
        assert "meeting" in t1_resp
        assert "Disha" in t1_resp
        assert "reaching home" in t1_resp
        assert "What would you like to do" in t1_resp
        assert session.pending_actions[0]["description"].startswith("continue Project LUME")

        # 3. Turn 2: user replies with compound instruction
        turn2 = await pipeline.handle_phone_message(
            "drive_01",
            "don't reply to the mail, just react with a thumbs up, and also tell Disha I'll be reaching in another hour",
        )
        t2_resp = turn2["response_text"]
        # No transports are connected, so both actions are prepared, not sent.
        assert "prepared 2 actions" in t2_resp
        assert "nothing was delivered" in t2_resp
        compound = turn2["action"]
        assert compound["type"] == "compound_prepare"
        statuses = {a["target"]: a["status"] for a in compound["actions"]}
        assert statuses["RazorClub"] == "not_executed"
        assert statuses["Disha"] == "not_sent"

        # 4. Turn 3: user concludes the call
        turn3 = await pipeline.handle_phone_message("drive_01", "no that's all, bye")
        assert turn3["digest_status"] == "completed"
        assert "Talk to you later" in turn3["response_text"]

    @pytest.mark.asyncio
    async def test_voice_disambiguation_mail_workflow(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        session = await pipeline._start_call("drive_02", driving_scenario=True)

        # 1. User commands: open mail, send a mail to shravan saying Hi Sceptix
        turn1 = await pipeline.handle_phone_message(
            "drive_02",
            "open mail, send a mail to shravan saying Hi Sceptix",
        )
        t1_resp = turn1["response_text"]
        # Vioris detects multiple Shravans and asks for disambiguation
        assert "Which Shravan?" in t1_resp
        assert "Shravan G" in t1_resp
        assert "Shravan GK" in t1_resp
        assert "disambiguation" in session.metadata

        # 2. User clarifies: shravan gk
        turn2 = await pipeline.handle_phone_message("drive_02", "shravan gk")
        t2_resp = turn2["response_text"]
        assert "prepared a mail" in t2_resp
        assert "has not been sent" in t2_resp
        assert "Shravan GK" in t2_resp
        assert "Hi Sceptix" in t2_resp
        assert turn2["action"] is not None
        assert turn2["action"]["type"] == "send_mail"
        assert turn2["action"]["recipient"] == "Shravan GK"
        assert turn2["action"]["email"] == "shravan.gk@sceptix.com"
        assert turn2["action"]["body"] == "Hi Sceptix"
        assert turn2["action"]["status"] == "not_sent"
        assert "disambiguation" not in session.metadata

        # 3. User finishes
        turn3 = await pipeline.handle_phone_message("drive_02", "bye")
        assert turn3["digest_status"] == "completed"


# ── user-defined call triggers ───────────────────────────────────────────────


class TestUserCallTriggers:
    @pytest.mark.asyncio
    async def test_time_trigger_starts_call_and_marks_fired(self, tmp_path):
        fixed_now = datetime(2026, 9, 17, 19, 1, tzinfo=UTC)
        store = CallTriggerStore(tmp_path / "triggers.json")
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(
            hub=hub, gmail=FakeGmail(connected=False), trigger_store=store
        )
        # Keep the daily digest out of the way so this is definitely a user trigger.
        pipeline._schedule.trigger_hours = [0]

        with patch("packages.shared.call_triggers._now", return_value=fixed_now):
            trigger = store.create(kind="time", at="19:00", label="Evening check-in")
            call_id = await pipeline.tick()

        assert call_id is not None
        assert hub.messages[0]["payload"]["reason"] == "Evening check-in"
        assert store.get(trigger.trigger_id).last_fired_at is not None
        assert pipeline._schedule.last_digest is None  # user trigger didn't consume it

    @pytest.mark.asyncio
    async def test_task_trigger_adds_completion_to_digest(self, tmp_path):
        fixed_now = datetime(2026, 9, 17, 19, 1, tzinfo=UTC)
        store = CallTriggerStore(tmp_path / "triggers.json")
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(
            hub=hub, gmail=FakeGmail(connected=False), trigger_store=store
        )
        pipeline._schedule.trigger_hours = [0]
        task = {
            "task_id": "t7",
            "request": "Deploy the new build",
            "status": "completed",
            "updated_at": (fixed_now + timedelta(minutes=1)).isoformat(),
        }

        with patch("packages.shared.call_triggers._now", return_value=fixed_now):
            trigger = store.create(kind="task_completed", match="deploy")
            call_id = await pipeline.tick(completed_tasks=[task])

        assert call_id is not None
        session = pipeline.get_session(call_id)
        assert session.digest.items[0].reference_id == "t7"
        assert "Deploy the new build" in session.digest.items[0].summary
        assert "Task completed" in hub.messages[0]["payload"]["reason"]
        assert store.get(trigger.trigger_id).fired_task_ids == ["t7"]

    @pytest.mark.asyncio
    async def test_handle_call_end_completes_and_clears_trust(self):
        hub = FakeHub()
        pipeline = ProactiveCallPipeline(hub=hub, gmail=FakeGmail(connected=False))
        pipeline._schedule.trigger_hours = [datetime.now(UTC).hour]
        call_id = await pipeline.tick()
        assert pipeline.dialogue.trust.session is not None

        assert await pipeline.handle_call_end(call_id) is True
        assert pipeline.get_session(call_id).status == DigestStatus.COMPLETED
        assert pipeline.dialogue.trust.session is None
        assert await pipeline.handle_call_end("missing") is False

    @pytest.mark.asyncio
    async def test_failed_call_is_not_marked_fired(self, tmp_path):
        fixed_now = datetime(2026, 9, 17, 19, 1, tzinfo=UTC)
        store = CallTriggerStore(tmp_path / "triggers.json")
        pipeline = ProactiveCallPipeline(
            hub=FakeHub(), gmail=FakeGmail(connected=False), trigger_store=store
        )
        pipeline._schedule.trigger_hours = [0]

        with patch("packages.shared.call_triggers._now", return_value=fixed_now):
            trigger = store.create(kind="time", at="19:00")
            with patch.object(
                pipeline, "_start_call", new=AsyncMock(side_effect=RuntimeError("no phone"))
            ):
                with pytest.raises(RuntimeError, match="no phone"):
                    await pipeline.tick()

        assert store.get(trigger.trigger_id).last_fired_at is None
