"""Tests for the intent classifier (Phase 7.1)."""

import pytest
from packages.shared.intent_classifier import (
    classify_intent,
    ClassifiedIntent,
    IntentType,
)


class TestQueryClassification:
    def test_time_query(self):
        r = classify_intent("what time is it")
        assert r.intent_type == IntentType.QUERY
        assert r.is_simple
        assert r.matched_pattern == "time_query"

    def test_calendar_query(self):
        r = classify_intent("what's on my calendar")
        assert r.intent_type == IntentType.QUERY
        assert r.is_simple

    def test_email_query(self):
        r = classify_intent("what's in my inbox")
        assert r.intent_type == IntentType.QUERY
        assert r.is_simple

    def test_unread_query(self):
        r = classify_intent("any unread messages")
        assert r.intent_type == IntentType.QUERY
        assert r.is_simple

    def test_status_query(self):
        r = classify_intent("check status")
        assert r.intent_type == IntentType.QUERY
        assert r.is_simple

    def test_show_query(self):
        r = classify_intent("show me my recent files")
        assert r.intent_type == IntentType.QUERY
        assert r.is_simple

    def test_who_query(self):
        r = classify_intent("who is John")
        assert r.intent_type == IntentType.QUERY

    def test_where_query(self):
        r = classify_intent("where is my phone")
        assert r.intent_type == IntentType.QUERY

    def test_when_query(self):
        r = classify_intent("when is the meeting")
        assert r.intent_type == IntentType.QUERY

    def test_why_query(self):
        r = classify_intent("why is this failing")
        assert r.intent_type == IntentType.QUERY

    def test_short_question_heuristic(self):
        r = classify_intent("hello?")
        assert r.intent_type == IntentType.QUERY
        assert r.confidence == 0.6

    def test_weather_query(self):
        r = classify_intent("what's the weather like")
        assert r.intent_type == IntentType.QUERY
        assert r.is_simple


class TestCommandClassification:
    def test_stop_command(self):
        r = classify_intent("stop")
        assert r.intent_type == IntentType.COMMAND
        assert r.is_simple
        assert r.matched_pattern == "stop_command"

    def test_open_command(self):
        r = classify_intent("open chrome")
        assert r.intent_type == IntentType.COMMAND
        assert r.is_simple

    def test_reminder_command(self):
        r = classify_intent("set a reminder for 3pm")
        assert r.intent_type == IntentType.COMMAND
        assert r.is_simple

    def test_remind_me(self):
        r = classify_intent("remind me to call mom")
        assert r.intent_type == IntentType.COMMAND
        assert r.is_simple

    def test_screenshot_command(self):
        r = classify_intent("take a screenshot")
        # "take" isn't matched but "screenshot" is
        assert r.intent_type == IntentType.COMMAND
        assert r.is_simple

    def test_lock_command(self):
        r = classify_intent("lock my computer")
        assert r.intent_type == IntentType.COMMAND

    def test_toggle_command(self):
        r = classify_intent("turn on the lights")
        assert r.intent_type == IntentType.COMMAND

    def test_send_command(self):
        r = classify_intent("send an email")
        assert r.intent_type == IntentType.COMMAND

    def test_single_word_falls_to_unknown(self):
        r = classify_intent("spotify")
        assert r.intent_type == IntentType.UNKNOWN


class TestComplexClassification:
    def test_compound_conjunction(self):
        r = classify_intent("what time is it and open chrome")
        assert r.intent_type == IntentType.COMPLEX
        assert not r.is_simple
        assert r.matched_pattern == "compound_conjunction"

    def test_also_conjunction(self):
        r = classify_intent("check my email also tell me the time")
        assert r.intent_type == IntentType.COMPLEX

    def test_then_conjunction(self):
        r = classify_intent("open chrome then take a screenshot")
        assert r.intent_type == IntentType.COMPLEX

    def test_sequence_markers(self):
        r = classify_intent("first open chrome, next take a screenshot")
        assert r.intent_type == IntentType.COMPLEX
        assert r.matched_pattern == "sequence_markers"

    def test_negation_with_continuation(self):
        r = classify_intent("don't reply to the mail but send a text")
        assert r.intent_type == IntentType.COMPLEX

    def test_multi_action_draft(self):
        r = classify_intent("draft an email and send it")
        assert r.intent_type == IntentType.COMPLEX

    def test_compound_request(self):
        r = classify_intent("can you check my email and also open chrome")
        assert r.intent_type == IntentType.COMPLEX

    def test_mixed_query_and_command(self):
        r = classify_intent("what time is it and open chrome")
        assert r.intent_type == IntentType.COMPLEX
        assert r.matched_pattern == "compound_conjunction"

    def test_hypothetical(self):
        r = classify_intent("what if I delete this file")
        assert r.intent_type == IntentType.COMPLEX


class TestUnknownClassification:
    def test_empty_string(self):
        r = classify_intent("")
        assert r.intent_type == IntentType.UNKNOWN
        assert r.confidence == 0.0

    def test_unrecognized_text(self):
        r = classify_intent("banana random sentence with no patterns")
        # "with" matches nothing, so should be UNKNOWN
        assert r.intent_type == IntentType.UNKNOWN

    def test_gibberish(self):
        r = classify_intent("asdfghjkl")
        assert r.intent_type == IntentType.UNKNOWN


class TestClassifiedIntent:
    def test_is_simple_query(self):
        r = classify_intent("what time is it")
        assert r.is_simple is True

    def test_is_simple_command(self):
        r = classify_intent("open chrome")
        assert r.is_simple is True

    def test_is_not_simple_complex(self):
        r = classify_intent("open chrome and check email")
        assert r.is_simple is False

    def test_is_not_simple_unknown(self):
        r = classify_intent("banana random sentence")
        assert r.is_simple is False

    def test_confidence_range(self):
        for text in ["stop", "what time is it", "open chrome and check email", "banana"]:
            r = classify_intent(text)
            assert 0.0 <= r.confidence <= 1.0
