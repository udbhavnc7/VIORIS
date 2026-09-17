"""Tests for Phase 8.4 — explainability module."""

import pytest

from packages.shared.explainability import (
    ExplainabilityEngine,
    ReasoningStore,
    StepReasoning,
    record_planning_reasoning,
)


@pytest.fixture
def store(tmp_path):
    return ReasoningStore(db_path=tmp_path / "reasoning.db")


@pytest.fixture
def engine(store):
    return ExplainabilityEngine(reasoning_store=store)


class TestReasoningStore:
    def test_store_and_retrieve(self, store):
        r = StepReasoning(
            task_id="t1",
            step_id="s1",
            tool="gmail.read_unread",
            chosen_reason="User asked to check email",
            alternatives=[
                {"tool": "whatsapp.read_digest", "rejected": True, "reason": "User said email, not WhatsApp"}
            ],
            risk_rationale="Read-only, observe tier",
            user_request="check my email",
        )
        rid = store.store(r)
        assert rid.startswith("reason_")

        retrieved = store.get_by_step("t1", "s1")
        assert retrieved is not None
        assert retrieved.tool == "gmail.read_unread"
        assert retrieved.chosen_reason == "User asked to check email"
        assert len(retrieved.alternatives) == 1

    def test_get_by_task(self, store):
        store.store(StepReasoning(task_id="t1", step_id="s1", tool="a", chosen_reason="r1"))
        store.store(StepReasoning(task_id="t1", step_id="s2", tool="b", chosen_reason="r2"))
        store.store(StepReasoning(task_id="t2", step_id="s3", tool="c", chosen_reason="r3"))

        results = store.get_by_task("t1")
        assert len(results) == 2
        assert results[0].step_id == "s1"
        assert results[1].step_id == "s2"

    def test_search_by_tool(self, store):
        store.store(StepReasoning(task_id="t1", step_id="s1", tool="gmail.read_unread", chosen_reason="r1"))
        store.store(StepReasoning(task_id="t1", step_id="s2", tool="gmail.read_unread", chosen_reason="r2"))
        store.store(StepReasoning(task_id="t1", step_id="s3", tool="calendar.read_upcoming", chosen_reason="r3"))

        results = store.search_by_tool("gmail.read_unread")
        assert len(results) == 2

    def test_search_by_request(self, store):
        store.store(StepReasoning(task_id="t1", step_id="s1", tool="a", chosen_reason="check email", user_request="check my email"))
        store.store(StepReasoning(task_id="t1", step_id="s2", tool="b", chosen_reason="open app", user_request="open vs code"))

        results = store.search_by_request("email")
        assert len(results) == 1
        assert results[0].tool == "a"

    def test_recent(self, store):
        for i in range(5):
            store.store(StepReasoning(task_id=f"t{i}", step_id=f"s{i}", tool=f"tool_{i}", chosen_reason=f"reason_{i}"))

        results = store.recent(limit=3)
        assert len(results) == 3
        # All have same timestamp, so order is by insertion (all 3 exist)
        tool_names = {r.tool for r in results}
        assert len(tool_names) == 3

    def test_get_nonexistent_returns_none(self, store):
        assert store.get_by_step("nonexistent", "nonexistent") is None


class TestExplainabilityEngine:
    def test_explain_step(self, engine, store):
        store.store(StepReasoning(
            task_id="t1", step_id="s1", tool="gmail.read_unread",
            chosen_reason="User asked to check email",
            alternatives=[{"tool": "whatsapp.read_digest", "rejected": True, "reason": "Wrong channel"}],
            risk_rationale="Read-only action",
        ))
        explanation = engine.explain_step("t1", "s1")
        assert "gmail.read_unread" in explanation
        assert "check email" in explanation
        assert "whatsapp.read_digest" in explanation

    def test_explain_step_not_found(self, engine):
        explanation = engine.explain_step("nonexistent", "nonexistent")
        assert "No reasoning found" in explanation

    def test_explain_task(self, engine, store):
        store.store(StepReasoning(task_id="t1", step_id="s1", tool="a", chosen_reason="first step"))
        store.store(StepReasoning(task_id="t1", step_id="s2", tool="b", chosen_reason="second step"))

        explanation = engine.explain_task("t1")
        assert "first step" in explanation
        assert "second step" in explanation

    def test_explain_task_not_found(self, engine):
        explanation = engine.explain_task("nonexistent")
        assert "No reasoning found" in explanation

    def test_explain_recent_with_query(self, engine, store):
        store.store(StepReasoning(task_id="t1", step_id="s1", tool="gmail.read_unread", chosen_reason="check email"))
        store.store(StepReasoning(task_id="t1", step_id="s2", tool="calendar.read_upcoming", chosen_reason="check calendar"))

        explanation = engine.explain_recent(query="email")
        assert "gmail.read_unread" in explanation
        assert "calendar" not in explanation

    def test_explain_recent_no_query(self, engine, store):
        store.store(StepReasoning(task_id="t1", step_id="s1", tool="a", chosen_reason="reason"))
        explanation = engine.explain_recent()
        assert "reason" in explanation

    def test_why_tool(self, engine, store):
        store.store(StepReasoning(
            task_id="t1", step_id="s1", tool="gmail.read_unread",
            chosen_reason="User asked about email",
            alternatives=[{"tool": "whatsapp.read_digest", "rejected": True, "reason": "Not messaging"}],
        ))
        explanation = engine.why_tool("gmail.read_unread")
        assert "gmail.read_unread" in explanation
        assert "whatsapp.read_digest" in explanation

    def test_why_tool_not_used(self, engine):
        explanation = engine.why_tool("nonexistent_tool")
        assert "haven't used" in explanation


class TestRecordPlanningReasoning:
    def test_record_and_retrieve(self, store):
        rid = record_planning_reasoning(
            store,
            task_id="t1",
            step_id="s1",
            tool="gmail.read_unread",
            chosen_reason="User wants email digest",
            alternatives=[{"tool": "other", "rejected": True, "reason": "less relevant"}],
            risk_rationale="Observe tier, no side effects",
            user_request="check my email",
        )
        assert rid.startswith("reason_")

        r = store.get_by_step("t1", "s1")
        assert r is not None
        assert r.tool == "gmail.read_unread"
        assert r.chosen_reason == "User wants email digest"
        assert r.risk_rationale == "Observe tier, no side effects"
        assert len(r.alternatives) == 1
