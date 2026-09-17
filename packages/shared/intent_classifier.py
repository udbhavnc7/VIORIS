"""
Intent Classifier (Phase 7.1).

Routes simple requests away from the full LLM planner to a fast, local
heuristic path. The classifier determines whether a user's utterance is simple
enough for direct execution or complex enough to require the full planner.

Classification tiers:
  - QUERY: read-only questions ("what time is it", "check my calendar")
  - COMMAND: single action ("open chrome", "stop", "set a reminder")
  - COMPLEX: multi-step, multi-intent, or ambiguous — needs full planner
  - UNKNOWN: unclassifiable — falls back to full planner

The classifier is deliberately conservative: when in doubt, it escalates to the
planner. False negatives (treating complex as simple) are more expensive than
false positives (running the planner on simple requests).

No LLM call is needed for classification — this uses regex patterns and keyword
sets, keeping latency under 1ms. A future version can swap in a tiny local
model (Phi-3-mini) for fuzzy classification if pattern coverage proves
insufficient.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    QUERY = "query"
    COMMAND = "command"
    COMPLEX = "complex"
    UNKNOWN = "unknown"


@dataclass
class ClassifiedIntent:
    intent_type: IntentType
    confidence: float  # 0.0–1.0
    matched_pattern: str | None = None  # which pattern matched (for debugging)

    @property
    def is_simple(self) -> bool:
        return self.intent_type in (IntentType.QUERY, IntentType.COMMAND)


# ---------------------------------------------------------------------------
# Pattern banks — each maps a regex to (IntentType, description)
# ---------------------------------------------------------------------------

_QUERY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bwhat time\b", re.I), "time_query"),
    (re.compile(r"\btell.*time\b", re.I), "time_query"),
    (re.compile(r"\bwhat(?:'s| is) the (?:date|day)\b", re.I), "date_query"),
    (re.compile(r"\bwhat(?:'s| is) (?:the )?weather\b", re.I), "weather_query"),
    (re.compile(r"\bhow (?:many|much|long|old)\b", re.I), "count_query"),
    (re.compile(r"\bwho (?:is|was|are)\b", re.I), "who_query"),
    (re.compile(r"\bwhere (?:is|was|are)\b", re.I), "where_query"),
    (re.compile(r"\bwhen (?:is|was|are|do|did|will)\b", re.I), "when_query"),
    (re.compile(r"\bwhy (?:is|was|are|do|did|will)\b", re.I), "why_query"),
    (re.compile(r"\b(?:show|tell|give) me\b", re.I), "show_query"),
    (re.compile(r"\bcheck\b", re.I), "check_query"),
    (re.compile(r"\bstatus\b", re.I), "status_query"),
    (re.compile(r"\bunread\b", re.I), "unread_query"),
    (re.compile(r"\bupcoming\b", re.I), "upcoming_query"),
    (re.compile(r"\brecent\b", re.I), "recent_query"),
    (re.compile(r"\b(?:do I|have I|did I) (?:have|has|get|got)\b", re.I), "possession_query"),
    (re.compile(r"\bis (?:there|it)\b", re.I), "existence_query"),
    (re.compile(r"\bwhat(?:'s| is) on (?:my )?calendar\b", re.I), "calendar_query"),
    (re.compile(r"\bwhat(?:'s| is) (?:in|on) my (?:inbox|mail|email)\b", re.I), "email_query"),
    (re.compile(r"\bany (?:new|unread|important)\b", re.I), "new_items_query"),
]

_COMMAND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:stop|halt|cancel|abort|kill)\b", re.I), "stop_command"),
    (re.compile(r"\bopen\b", re.I), "open_command"),
    (re.compile(r"\blaunch\b", re.I), "launch_command"),
    (re.compile(r"\bstart\b", re.I), "start_command"),
    (re.compile(r"\bset (?:a )?reminder\b", re.I), "reminder_command"),
    (re.compile(r"\bremind me\b", re.I), "reminder_command"),
    (re.compile(r"\bclose\b", re.I), "close_command"),
    (re.compile(r"\bplay\b", re.I), "play_command"),
    (re.compile(r"\bpause\b", re.I), "pause_command"),
    (re.compile(r"\bvolume\b", re.I), "volume_command"),
    (re.compile(r"\bmute\b", re.I), "mute_command"),
    (re.compile(r"\bscreenshot\b", re.I), "screenshot_command"),
    (re.compile(r"\block\b", re.I), "lock_command"),
    (re.compile(r"\bshutdown\b", re.I), "shutdown_command"),
    (re.compile(r"\brestart\b", re.I), "restart_command"),
    (re.compile(r"\bsend\b", re.I), "send_command"),
    (re.compile(r"\bcall\b", re.I), "call_command"),
    (re.compile(r"\btext\b", re.I), "text_command"),
    (re.compile(r"\bturn (?:on|off)\b", re.I), "toggle_command"),
]

# Patterns that indicate multi-intent or compound requests — always COMPLEX
_COMPLEX_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:and|also|then|plus|additionally|while you(?:'re| are))\b", re.I), "compound_conjunction"),
    (re.compile(r"\b(?:but|however|although|though)\b", re.I), "contrast_conjunction"),
    (re.compile(r"\b(?:first|second|third|next|finally|after that|before that)\b", re.I), "sequence_markers"),
    (re.compile(r"\b(?:don'?t|do not|never|stop)\b.*\b(?:but|and|also)\b", re.I), "negation_with_continuation"),
    (re.compile(r"\b(?:draft|write|compose|create)\b.*\b(?:and|then|also)\b", re.I), "multi_action_draft"),
    (re.compile(r"\b(?:send|email|message|text)\b.*\b(?:and|then|also)\b", re.I), "multi_action_communicate"),
    (re.compile(r"\b(?:check|read|look)\b.*\b(?:and|then|also)\b", re.I), "multi_action_observe"),
    (re.compile(r"\bwhat if\b", re.I), "hypothetical"),
    (re.compile(r"\b(?:can you|could you|would you)\b.*\b(?:and|also|then)\b", re.I), "compound_request"),
]


def classify_intent(transcript: str) -> ClassifiedIntent:
    """Classify a user transcript into an intent type.

    Returns a ClassifiedIntent with type, confidence, and the matched pattern
    name (for debugging/logging). When patterns conflict, the most specific
    wins: COMPLEX > COMMAND > QUERY > UNKNOWN.

    This is a pure function with no side effects — safe to call from any path.
    """
    text = transcript.strip()
    if not text:
        return ClassifiedIntent(IntentType.UNKNOWN, 0.0)

    # Check for complex patterns first — compound/multi-intent always escalates
    for pattern, name in _COMPLEX_PATTERNS:
        if pattern.search(text):
            logger.debug("classified as COMPLEX via pattern '%s': %s", name, text[:60])
            return ClassifiedIntent(IntentType.COMPLEX, 0.95, name)

    # Collect all matching simple patterns
    query_matches = []
    command_matches = []

    for pattern, name in _QUERY_PATTERNS:
        if pattern.search(text):
            query_matches.append(name)

    for pattern, name in _COMMAND_PATTERNS:
        if pattern.search(text):
            command_matches.append(name)

    # If both query and command patterns match, it's likely complex
    if query_matches and command_matches:
        logger.debug(
            "classified as COMPLEX (mixed query+command): queries=%s commands=%s",
            query_matches,
            command_matches,
        )
        return ClassifiedIntent(IntentType.COMPLEX, 0.85, f"mixed:{query_matches[0]}+{command_matches[0]}")

    # Single-category matches
    if command_matches:
        logger.debug("classified as COMMAND via '%s': %s", command_matches[0], text[:60])
        # Multiple command pattern matches in same category is still COMMAND
        return ClassifiedIntent(IntentType.COMMAND, 0.9 if len(command_matches) == 1 else 0.8, command_matches[0])

    if query_matches:
        logger.debug("classified as QUERY via '%s': %s", query_matches[0], text[:60])
        return ClassifiedIntent(IntentType.QUERY, 0.9, query_matches[0])

    # Heuristic fallback: very short utterances ending with ? are queries
    words = text.split()
    if len(words) <= 4 and text.strip().endswith("?"):
        return ClassifiedIntent(IntentType.QUERY, 0.6, "short_question_heuristic")

    # Default: escalate to planner
    logger.debug("classified as UNKNOWN (no pattern match): %s", text[:60])
    return ClassifiedIntent(IntentType.UNKNOWN, 0.3, None)
