"""
Explainability Dialogue Handler (Phase 10.2).

Wires the existing ExplainabilityEngine into the dialogue system so users can
ask "why did you do that?" via voice or text and get a natural language answer.

Detects explainability queries in the intent classifier and routes them to the
ExplainabilityEngine instead of the full planner.

Usage:
    handler = ExplainabilityHandler(explain_engine)
    response = handler.handle("why did you open chrome")
    # -> "I used system.open_app because you asked to open chrome..."
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from packages.shared.explainability import ExplainabilityEngine

logger = logging.getLogger(__name__)

# Patterns that indicate an explainability query
_WHY_PATTERNS = [
    re.compile(r"\bwhy (?:did|do|was|were|have|had|would|could|should) you\b", re.I),
    re.compile(r"\bwhy (?:did|do|was|were|have|had) (?:it|that|this|the)\b", re.I),
    re.compile(r"\bwhat (?:made|caused|prompted) you\b", re.I),
    re.compile(r"\bwhat(?:'s| is) the (?:reason|cause|purpose) (?:for|of|behind)\b", re.I),
    re.compile(r"\bexplain\b", re.I),
    re.compile(r"\bhow (?:did|do|was) (?:you|that|it)\b", re.I),
    re.compile(r"\bwhat (?:happened|did (?:you|it|that))\b", re.I),
    re.compile(r"\btell me (?:about|what|why|how)\b", re.I),
    re.compile(r"\bwhat did you (?:just )?(?:do|execute|run|perform)\b", re.I),
    re.compile(r"\bwhat (?:was|is) (?:that|this|it)\b", re.I),
]

# Patterns that specify WHAT to explain
_TARGET_PATTERNS = [
    re.compile(r"\b(?:about|for|to|regarding|with)\s+(.+?)(?:\s*$|\?)", re.I),
    re.compile(r"\b(?:the|my|a|an)\s+(.+?)(?:\s+(?:action|step|decision|task|command|tool))", re.I),
    re.compile(r"\buse\s+(.+?)(?:\s*$|\?)", re.I),
]


@dataclass
class ExplainabilityResponse:
    """Response from an explainability query."""

    explanation: str
    found: bool = True
    query_type: str = "general"  # "step", "task", "tool", "general"

    def to_dict(self) -> dict:
        return {
            "explanation": self.explanation,
            "found": self.found,
            "query_type": self.query_type,
        }


class ExplainabilityHandler:
    """Handles "why did you do that?" queries in the dialogue system.

    Detects explainability intents and routes them to the ExplainabilityEngine
    to retrieve and format the reasoning behind past actions.
    """

    def __init__(self, engine: ExplainabilityEngine) -> None:
        self.engine = engine

    def is_explain_query(self, text: str) -> bool:
        """Check if a text is an explainability query."""
        return any(p.search(text) for p in _WHY_PATTERNS)

    def handle(self, text: str) -> ExplainabilityResponse:
        """Process an explainability query and return a natural language response.

        Analyzes the query to determine what the user wants explained:
        - "why did you open chrome?" → recent explanation for system.open_app
        - "why did you send that email?" → explanation for whatsapp.send_message
        - "what did you just do?" → most recent actions
        - "explain your last decision" → most recent task reasoning
        """
        if not text or not text.strip():
            return ExplainabilityResponse(
                explanation="What would you like me to explain?",
                found=False,
            )

        text = text.strip()

        # Try to extract a specific target (tool name, action, etc.)
        target = self._extract_target(text)

        # If the query mentions a specific tool name directly
        if target and self._looks_like_tool_name(target):
            return self._explain_tool(target)

        # Check for tool names embedded in the text (e.g. "why did you use gmail.read_unread")
        tool_in_text = self._find_tool_in_text(text)
        if tool_in_text:
            return self._explain_tool(tool_in_text)

        # General "why / what did you do" query → explain recent
        return self._explain_recent(text)

    def _extract_target(self, text: str) -> str | None:
        """Try to extract what the user wants explained."""
        for pattern in _TARGET_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1).strip()
        return None

    def _looks_like_tool_name(self, text: str) -> bool:
        """Check if text looks like a tool name (e.g. 'open_app', 'gmail.read')."""
        return bool(re.match(r"^[a-z_]+\.[a-z_]+$", text.lower().strip()))

    def _find_tool_in_text(self, text: str) -> str | None:
        """Find a tool name embedded anywhere in the text."""
        # Match tool-like patterns: word.word (e.g. gmail.read_unread, system.open_app)
        matches = re.findall(r"\b([a-z_]+\.[a-z_]+)\b", text.lower())
        for m in matches:
            if self._looks_like_tool_name(m):
                return m
        return None

    def _is_about_recent(self, text: str) -> bool:
        """Check if the query is about recent actions."""
        recent_keywords = ["just", "last", "recent", "recently", "that", "this", "it"]
        return any(kw in text.lower() for kw in recent_keywords)

    def _explain_tool(self, tool_name: str) -> ExplainabilityResponse:
        """Explain why a specific tool was used."""
        explanation = self.engine.why_tool(tool_name)
        found = tool_name in explanation.lower() or "used" in explanation.lower()
        return ExplainabilityResponse(
            explanation=explanation,
            found=found,
            query_type="tool",
        )

    def _explain_recent(self, text: str) -> ExplainabilityResponse:
        """Explain recent actions."""
        explanation = self.engine.explain_recent()
        found = "i haven't" not in explanation.lower()
        return ExplainabilityResponse(
            explanation=explanation,
            found=found,
            query_type="general",
        )

    def _search_and_explain(self, target: str) -> ExplainabilityResponse:
        """Search for a target and explain what was found."""
        # Try as a request search
        explanation = self.engine.explain_recent(query=target)
        found = "i haven't" not in explanation.lower() and "no reasoning" not in explanation.lower()
        return ExplainabilityResponse(
            explanation=explanation,
            found=found,
            query_type="general",
        )

    def _extract_search_query(self, text: str) -> str | None:
        """Extract a search query from the text."""
        # Remove common explainability query phrases
        noise = [
            "why did you", "why do you", "what made you", "explain your",
            "tell me about", "what happened", "what did you just",
            "what did you", "how did you", "the", "my", "a", "an",
            "that", "this", "it", "just", "do", "did", "you",
        ]
        result = text.lower()
        for word in noise:
            result = result.replace(word, " ")
        result = re.sub(r"\s+", " ", result).strip()
        return result if len(result) > 2 else None
