"""Query planner for the episodic/semantic memory split (Phase 7.2).

A question about *what happened* (timestamped events, actions, conversation
turns) goes to the episodic collection. A question about *what is known*
(facts, preferences, people, credentials) goes to the semantic collection.
The choice is made here from the user's wording — never from content returned
by memory or from an LLM — so retrieval stays deterministic and auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from packages.shared.memory_split import MemoryType

# Each signal is a named regex matched against the lowercased question.
_EPISODIC_SIGNALS: tuple[tuple[str, str], ...] = (
    ("time_reference", r"\b(?:yesterday|last night|this morning|earlier today|"
                       r"last week|at \d{1,2}(?::\d{2})?\s*(?:am|pm)?|"
                       r"on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b"),
    ("event", r"\b(?:what happened|what did (?:i|you|we)|when did|"
              r"timeline|history|recent actions?|log of)\b"),
    ("action", r"\b(?:did you|you said|you did|i asked|i said|we did|"
               r"you just|earlier)\b"),
)

_SEMANTIC_SIGNALS: tuple[tuple[str, str], ...] = (
    ("identity", r"\b(?:who is|what is my|what's my|where is|"
                 r"what does .{1,60} mean|define)\b"),
    ("fact", r"\b(?:prefer|preferences?|password|pin|address|email|phone number|"
             r"contact|manager|wife|spouse|birthday|wifi)\b"),
    ("knowledge", r"\b(?:tell me about|know about|meaning of|settings?)\b"),
)

_EPISODIC = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _EPISODIC_SIGNALS)
_SEMANTIC = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _SEMANTIC_SIGNALS)


@dataclass(frozen=True)
class MemoryQueryPlan:
    """Which collection to query and why."""

    memory_type: MemoryType
    reason: str
    signals: tuple[str, ...]


def plan_memory_query(question: str) -> MemoryQueryPlan:
    """Route a natural-language question to one of the two collections."""
    text = (question or "").strip().lower()
    episodic_signals = tuple(name for name, rx in _EPISODIC if rx.search(text))
    semantic_signals = tuple(name for name, rx in _SEMANTIC if rx.search(text))

    if episodic_signals and len(episodic_signals) >= len(semantic_signals):
        return MemoryQueryPlan(
            memory_type=MemoryType.EPISODIC,
            reason="question refers to timed events or things that happened",
            signals=episodic_signals,
        )
    if semantic_signals:
        return MemoryQueryPlan(
            memory_type=MemoryType.SEMANTIC,
            reason="question asks about a known fact, preference, or person",
            signals=semantic_signals,
        )
    return MemoryQueryPlan(
        memory_type=MemoryType.SEMANTIC,
        reason="no temporal/event signal; facts and preferences are the safe default",
        signals=(),
    )
