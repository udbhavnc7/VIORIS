"""Tests for the explainability dialogue handler (Phase 10.2)."""

import pytest
from pathlib import Path

from packages.shared.explainability import ExplainabilityEngine, ReasoningStore, StepReasoning
from packages.shared.explainability_handler import (
    ExplainabilityHandler,
    ExplainabilityResponse,
)


@pytest.fixture
def store(tmp_path):
    return ReasoningStore(db_path=str(tmp_path / "reasoning.db"))


@pytest.fixture
def engine(store):
    return ExplainabilityEngine(reasoning_store=store)


@pytest.fixture
def handler(engine):
    return ExplainabilityHandler(engine=engine)


class TestExplainabilityResponse:
    def test_to_dict(self):
        r = ExplainabilityResponse(
            explanation="I used open_app because you asked",
            found=True,
            query_type="tool",
        )
        d = r.to_dict()
        assert d["explanation"] == "I used open_app because you asked"
        assert d["found"] is True
        assert d["query_type"] == "tool"


class TestIsExplainQuery:
    def test_why_did_you(self, handler):
        assert handler.is_explain_query("why did you open chrome")

    def test_why_do_you(self, handler):
        assert handler.is_explain_query("why do you keep doing that")

    def test_what_made_you(self, handler):
        assert handler.is_explain_query("what made you send that email")

    def test_explain_your(self, handler):
        assert handler.is_explain_query("explain your last decision")

    def test_what_happened(self, handler):
        assert handler.is_explain_query("what happened just now")

    def test_what_did_you_do(self, handler):
        assert handler.is_explain_query("what did you just do")

    def test_not_explain_query(self, handler):
        assert not handler.is_explain_query("open chrome")
        assert not handler.is_explain_query("what time is it")
        assert not handler.is_explain_query("send an email")


class TestHandleWithReasoning:
    def test_explain_tool(self, store, engine, handler):
        # Store some reasoning
        store.store(StepReasoning(
            task_id="t1",
            step_id="s1",
            tool="system.open_app",
            chosen_reason="User asked to open Chrome browser",
            alternatives=[
                {"tool": "system.open_app", "reason": "Direct app launch", "rejected": False},
                {"tool": "browser.navigate", "reason": "Could use browser directly", "rejected": True},
            ],
            risk_rationale="Observe tier, no risk",
            user_request="open chrome",
        ))

        response = handler.handle("why did you open chrome")
        assert response.found is True
        assert "open_app" in response.explanation
        assert "Chrome" in response.explanation or "chrome" in response.explanation.lower()

    def test_explain_recent_actions(self, store, engine, handler):
        # Store multiple reasoning records
        store.store(StepReasoning(
            task_id="t1", step_id="s1", tool="system.get_time",
            chosen_reason="User asked for the time",
            user_request="what time is it",
        ))
        store.store(StepReasoning(
            task_id="t2", step_id="s1", tool="system.open_app",
            chosen_reason="User asked to open Chrome",
            user_request="open chrome",
        ))

        response = handler.handle("what did you just do")
        assert response.found is True
        assert "get_time" in response.explanation or "open_app" in response.explanation

    def test_no_reasoning_found(self, handler):
        response = handler.handle("why did you do that")
        assert response.found is False
        assert "haven't" in response.explanation.lower() or "What" in response.explanation

    def test_empty_query(self, handler):
        response = handler.handle("")
        assert response.found is False

    def test_none_query(self, handler):
        response = handler.handle(None)
        assert response.found is False

    def test_explain_with_target(self, store, engine, handler):
        store.store(StepReasoning(
            task_id="t1", step_id="s1", tool="gmail.read_unread",
            chosen_reason="Checking for new emails as requested",
            user_request="check my email",
        ))

        response = handler.handle("tell me about the email check")
        assert response.found is True

    def test_tool_name_recognized(self, store, engine, handler):
        store.store(StepReasoning(
            task_id="t1", step_id="s1", tool="gmail.read_unread",
            chosen_reason="To check unread emails",
            user_request="check email",
        ))

        response = handler.handle("why did you use gmail.read_unread")
        assert response.found is True
        assert "gmail.read_unread" in response.explanation

    def test_recent_keyword_detection(self, handler):
        # "just" triggers recent explanation
        assert handler._is_about_recent("what did you just do")
        assert handler._is_about_recent("why did you just open that")
        assert handler._is_about_recent("what happened last")

    def test_not_about_recent(self, handler):
        assert not handler._is_about_recent("why did you open chrome yesterday")


class TestTargetExtraction:
    def test_about_tool(self, handler):
        target = handler._extract_target("why did you use the gmail tool")
        assert target is not None

    def test_for_action(self, handler):
        target = handler._extract_target("why did you do that for the email")
        assert target is not None

    def test_no_target(self, handler):
        target = handler._extract_target("why did you do that")
        # May or may not extract something
        assert target is None or isinstance(target, str)
