"""Tests for Phase 8.5 — Call Experience."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from packages.shared.call_experience import (
    AudioFrame,
    AudioStream,
    CallExperience,
    CallState,
    DigestItem,
    DigestSchedule,
    EmailReact,
    ProactiveDigest,
    RingEvent,
    RingSource,
)


class TestRingEvent:
    def test_create_incoming(self):
        event = RingEvent(call_id="c1", source=RingSource.INCOMING, caller_name="Disha")
        assert event.call_id == "c1"
        assert event.source == RingSource.INCOMING
        assert event.caller_name == "Disha"

    def test_create_proactive(self):
        event = RingEvent(call_id="c2", source=RingSource.PROACTIVE, caller_name="Vioris", reason="digest")
        assert event.source == RingSource.PROACTIVE
        assert event.reason == "digest"

    def test_to_json_roundtrip(self):
        event = RingEvent(call_id="c1", source=RingSource.INCOMING, caller_name="Disha", reason="test")
        data = event.to_json()
        restored = RingEvent.from_json(data)
        assert restored.call_id == event.call_id
        assert restored.source == event.source
        assert restored.caller_name == event.caller_name
        assert restored.reason == event.reason


class TestAudioStream:
    def test_push_frame(self):
        stream = AudioStream()
        frame = stream.push(b"audio_data", timestamp=1.0, sample_rate=16000)
        assert frame.data == b"audio_data"
        assert frame.timestamp == 1.0
        assert frame.sequence == 0
        assert stream.frames[0] is frame

    def test_sequence_increments(self):
        stream = AudioStream()
        f1 = stream.push(b"a", 1.0)
        f2 = stream.push(b"b", 2.0)
        assert f1.sequence == 0
        assert f2.sequence == 1

    def test_frame_to_json(self):
        frame = AudioFrame(data=b"x", timestamp=1.0, sequence=0)
        data = json.loads(frame.to_json())
        assert data["sequence"] == 0
        assert data["sample_rate"] == 16000

    def test_encode_audio(self):
        frame = AudioFrame(data=b"raw_audio", timestamp=1.0, sequence=0)
        assert frame.encode_audio() == b"raw_audio"


class TestEmailReact:
    def test_valid_reactions(self):
        react = EmailReact(email_id="e1", sender="Disha", subject="Re: Dinner", reaction="👍")
        valid, err = react.validate()
        assert valid
        assert err == ""

    def test_invalid_reaction(self):
        react = EmailReact(email_id="e1", sender="Disha", subject="Re: Dinner", reaction="fire")
        valid, err = react.validate()
        assert not valid
        assert "reaction must be one of" in err

    def test_no_reaction_valid(self):
        react = EmailReact(email_id="e1", sender="Disha", subject="Re: Dinner", reaction="None")
        valid, err = react.validate()
        assert valid

    def test_missing_email_id(self):
        react = EmailReact(email_id="", sender="Disha", subject="test")
        valid, err = react.validate()
        assert not valid
        assert "email_id required" in err

    def test_missing_sender(self):
        react = EmailReact(email_id="e1", sender="", subject="test")
        valid, err = react.validate()
        assert not valid
        assert "sender required" in err

    def test_to_dict(self):
        react = EmailReact(email_id="e1", sender="Disha", subject="Dinner", reaction="❤️")
        d = react.to_dict()
        assert d["email_id"] == "e1"
        assert d["reaction"] == "❤️"


class TestDigestSchedule:
    def test_trigger_at_8am(self):
        sched = DigestSchedule()
        now = datetime(2025, 1, 15, 8, 0, 0)
        assert sched.should_trigger(now)

    def test_no_trigger_outside_hours(self):
        sched = DigestSchedule()
        now = datetime(2025, 1, 15, 12, 0, 0)
        assert not sched.should_trigger(now)

    def test_no_repeat_within_hour(self):
        sched = DigestSchedule()
        now = datetime(2025, 1, 15, 8, 0, 0)
        sched.record_trigger(now)
        now2 = datetime(2025, 1, 15, 8, 30, 0)
        assert not sched.should_trigger(now2)

    def test_trigger_after_hour(self):
        sched = DigestSchedule()
        now = datetime(2025, 1, 15, 8, 0, 0)
        sched.record_trigger(now)
        now2 = datetime(2025, 1, 15, 13, 0, 0)
        assert sched.should_trigger(now2)

    def test_next_trigger_today(self):
        sched = DigestSchedule()
        now = datetime(2025, 1, 15, 9, 0, 0)
        nxt = sched.next_trigger(now)
        assert nxt.hour == 13

    def test_next_trigger_tomorrow(self):
        sched = DigestSchedule()
        now = datetime(2025, 1, 15, 20, 0, 0)
        nxt = sched.next_trigger(now)
        assert nxt.hour == 8
        assert nxt.day == 16

    def test_custom_hours(self):
        sched = DigestSchedule(custom_hours=[9, 17])
        now = datetime(2025, 1, 15, 9, 0, 0)
        assert sched.should_trigger(now)
        now2 = datetime(2025, 1, 15, 8, 0, 0)
        assert not sched.should_trigger(now2)


class TestProactiveDigest:
    def test_empty_digest(self):
        digest = ProactiveDigest(call_id="c1")
        assert digest.compose_spoken() == "Nothing new."

    def test_compose_with_items(self):
        items = [
            DigestItem("email", "Meeting at 3pm", "Boss", actionable=True),
            DigestItem("message", "Disha says hi", "Disha"),
        ]
        digest = ProactiveDigest(call_id="c1", items=items)
        spoken = digest.compose_spoken()
        assert "Disha says hi" in spoken
        assert "1 items need your attention" in spoken

    def test_compose_all_actionable(self):
        items = [
            DigestItem("email", "Urgent", "Boss", actionable=True),
            DigestItem("message", "Reply needed", "Disha", actionable=True),
        ]
        digest = ProactiveDigest(call_id="c1", items=items)
        spoken = digest.compose_spoken()
        assert "2 items need your attention" in spoken

    def test_to_dict(self):
        items = [DigestItem("email", "Test", "Boss")]
        digest = ProactiveDigest(call_id="c1", items=items)
        d = digest.to_dict()
        assert d["call_id"] == "c1"
        assert len(d["items"]) == 1


class TestCallExperience:
    def test_lifecycle(self):
        ce = CallExperience()
        assert ce.state == CallState.IDLE

        event = RingEvent(call_id="c1", source=RingSource.INCOMING, caller_name="Disha")
        ce.start_ring(event)
        assert ce.state == CallState.RINGING
        assert ce.call_id == "c1"

        ce.accept_call()
        assert ce.state == CallState.CONNECTED

        ce.start_active()
        assert ce.state == CallState.ACTIVE

        ce.end_call()
        assert ce.state == CallState.IDLE
        assert ce.call_id is None

    def test_state_handler(self):
        ce = CallExperience()
        transitions = []
        ce.on_state_change(CallState.RINGING, lambda old, new: transitions.append((old, new)))
        ce.start_ring(RingEvent(call_id="c1", source=RingSource.INCOMING, caller_name="test"))
        assert len(transitions) == 1
        assert transitions[0] == (CallState.IDLE, CallState.RINGING)

    def test_push_audio(self):
        ce = CallExperience()
        frame = ce.push_audio(b"audio", 1.0, 16000)
        assert frame.data == b"audio"
        assert len(ce.audio_stream.frames) == 1

    def test_get_digest(self):
        ce = CallExperience()
        items = [DigestItem("email", "Test", "Boss")]
        digest = ce.get_digest("c1", items)
        assert digest.call_id == "c1"
        assert len(digest.items) == 1

    def test_digest_trigger(self):
        ce = CallExperience()
        now = datetime(2025, 1, 15, 8, 0, 0)
        assert ce.check_digest_trigger(now)
        ce.trigger_digest(now)
        assert not ce.check_digest_trigger(now + timedelta(minutes=30))

    def test_invalid_state_transitions(self):
        ce = CallExperience()
        ce.accept_call()  # not ringing
        assert ce.state == CallState.IDLE
        ce.start_active()  # not connected
        assert ce.state == CallState.IDLE

    def test_end_call_resets(self):
        ce = CallExperience()
        ce.start_ring(RingEvent(call_id="c1", source=RingSource.INCOMING, caller_name="test"))
        ce.accept_call()
        ce.start_active()
        ce.end_call()
        assert ce.ring_event is None
