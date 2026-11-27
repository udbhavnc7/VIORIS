"""Deterministic query planning for the episodic/semantic memory split."""

import pytest

from packages.shared.memory_split import MemoryType
from services.memory_service.app.query_planner import plan_memory_query


@pytest.mark.parametrize(
    "question,expected",
    [
        ("what did I ask yesterday?", MemoryType.EPISODIC),
        ("what happened at 3pm last night", MemoryType.EPISODIC),
        ("when did you open Chrome earlier today", MemoryType.EPISODIC),
        ("show me the history of actions", MemoryType.EPISODIC),
        ("what did you say on Monday", MemoryType.EPISODIC),
        ("what's my WiFi password?", MemoryType.SEMANTIC),
        ("who is my manager", MemoryType.SEMANTIC),
        ("what are my preferences for notifications", MemoryType.SEMANTIC),
        ("tell me about Disha", MemoryType.SEMANTIC),
        ("what is the meaning of OKR", MemoryType.SEMANTIC),
        ("random question with no signals", MemoryType.SEMANTIC),
        ("", MemoryType.SEMANTIC),
    ],
)
def test_query_routes_to_the_right_collection(question, expected):
    plan = plan_memory_query(question)
    assert plan.memory_type == expected
    assert plan.reason


def test_plan_is_explainable_and_deterministic():
    a = plan_memory_query("what did I ask yesterday?")
    b = plan_memory_query("what did I ask yesterday?")
    assert a == b
    assert "time_reference" in a.signals
    assert "event" in a.signals
    assert a.memory_type.value in a.reason or "timed events" in a.reason


def test_semantic_signals_are_recorded():
    plan = plan_memory_query("what's my password?")
    assert plan.signals == ("identity", "fact")
    assert "known fact" in plan.reason
