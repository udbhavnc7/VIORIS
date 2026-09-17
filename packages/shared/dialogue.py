"""
Dialogue Orchestrator (Phase 8.6).

The layer above the task planner that holds real multi-intent, reference-aware,
decision-surfacing conversations on top of the permission system. This is what
turns Vioris from "an agent you open an app to talk to" into "an agent that's
present in your life."

Components:
  1. DialogueContext   — call-scoped reference memory (entities, tool results)
  2. MultiIntentDecomposer — split one utterance into N parallel sub-intents
  3. ActionabilityFlagger — detect items that look like they need a response
  4. CompoundCommandSplitter — parse one reply into multiple independent instructions
  5. ResponseComposer  — merge N results into one natural spoken sentence
  6. CallSessionTrust  — voiceprint at pickup grants verified session
  7. DialogueOrchestrator — the main coordinator tying it all together

This module is LLM-agnostic — it uses the LLM for understanding/generation
but all decomposition, routing, and safety logic is deterministic.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Dialogue Context — call-scoped reference memory
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Entity:
    """A named entity mentioned during a call session."""

    name: str
    aliases: list[str] = field(default_factory=list)
    entity_type: str = ""  # 'person', 'email', 'message', 'file', 'app', etc.
    channel: str = ""  # 'whatsapp', 'gmail', 'sms', etc.
    metadata: dict[str, Any] = field(default_factory=dict)
    mentioned_at: float = field(default_factory=time.monotonic)


@dataclass
class ToolResult:
    """A result from a tool call, stored for reference resolution."""

    tool: str
    result: dict[str, Any]
    timestamp: float = field(default_factory=time.monotonic)
    reference_id: str = ""  # e.g. "the mail", "her message"


class DialogueContext:
    """Call-scoped memory that stores recent entities and tool results.

    Separate from long-term semantic/episodic memory. Short-lived, per-call.
    Enables reference resolution: "the mail" → last email result, "her" →
    last person mentioned, "that" → last tool result.

    The context is cleared at the start of each new call.
    """

    def __init__(self, max_entities: int = 50, max_results: int = 20) -> None:
        self.max_entities = max_entities
        self.max_results = max_results
        self._entities: list[Entity] = []
        self._results: list[ToolResult] = []
        self._turn_count: int = 0
        self._call_started_at: float = time.monotonic()

    def add_entity(self, entity: Entity) -> None:
        """Track a named entity mentioned in the conversation."""
        self._entities.append(entity)
        if len(self._entities) > self.max_entities:
            self._entities = self._entities[-self.max_entities:]

    def add_result(self, result: ToolResult) -> None:
        """Store a tool result for later reference."""
        self._results.append(result)
        if len(self._results) > self.max_results:
            self._results = self._results[-self.max_results:]

    def increment_turn(self) -> None:
        self._turn_count += 1

    def resolve_reference(self, reference: str) -> Entity | ToolResult | None:
        """Resolve a pronoun or definite reference to the most recent match.

        Resolution order:
          1. Exact name match in entities
          2. Alias match in entities
          3. Type-based match ("the mail" → last email, "her" → last person)
          4. Reference ID match in tool results
        """
        ref_lower = reference.strip().lower()

        # 1. Exact entity name match
        for entity in reversed(self._entities):
            if entity.name.lower() == ref_lower:
                return entity

        # 2. Alias match
        for entity in reversed(self._entities):
            if any(a.lower() == ref_lower for a in entity.aliases):
                return entity

        # 3. Type-based shortcuts
        type_map = {
            "the mail": "email",
            "the email": "email",
            "the message": "message",
            "the text": "message",
            "the file": "file",
            "the document": "file",
        }
        for pattern, etype in type_map.items():
            if ref_lower == pattern or ref_lower.startswith(pattern):
                for entity in reversed(self._entities):
                    if entity.entity_type == etype:
                        return entity

        # Pronoun shortcuts
        if ref_lower in ("her", "him", "them", "that person"):
            # Return most recently mentioned person
            for entity in reversed(self._entities):
                if entity.entity_type == "person":
                    return entity

        # 4. Reference ID in tool results
        for result in reversed(self._results):
            if result.reference_id and result.reference_id.lower() == ref_lower:
                return result

        return None

    def last_entity_of_type(self, entity_type: str) -> Entity | None:
        """Get the most recent entity of a given type."""
        for entity in reversed(self._entities):
            if entity.entity_type == entity_type:
                return entity
        return None

    def last_result(self) -> ToolResult | None:
        """Get the most recent tool result."""
        return self._results[-1] if self._results else None

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def call_duration(self) -> float:
        return time.monotonic() - self._call_started_at

    def clear(self) -> None:
        """Reset for a new call session."""
        self._entities.clear()
        self._results.clear()
        self._turn_count = 0
        self._call_started_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot for debugging/logging."""
        return {
            "entities": [
                {"name": e.name, "type": e.entity_type, "channel": e.channel}
                for e in self._entities
            ],
            "results_count": len(self._results),
            "turn_count": self._turn_count,
            "call_duration": round(self.call_duration, 1),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Multi-Intent Decomposer
# ═══════════════════════════════════════════════════════════════════════════════

# Sentence-level splitting patterns for multi-intent utterances.
# These are deterministic (no LLM) for reliability.
_SPLITTERS = [
    # Conjunctions that join independent clauses
    r"\b(?:also|and also|plus|additionally|while you(?:'re| are) at it)\b",
    # Comma-separated instructions
    r"(?:,\s*(?:also\s+)?)",
    # Semicolons
    r";\s*",
    # Explicit separators
    r"\b(?:then|after that|before that|first|second|third|next)\b",
]

# Negation patterns that scope to a specific clause
_NEGATION_PATTERNS = [
    r"(?:don(?:'t|t)|do not|skip|instead of|rather than|not)\s+",
    r"(?:never|avoid)\s+",
]

# Actionability signals — phrases that suggest an item needs a response
_ACTIONABILITY_SIGNALS = [
    r"(?:asking|asks?|requesting|requests?|wants? to know|wondering)",
    r"(?:waiting for|expecting|needs? a reply|needs? a response)",
    r"(?:question|inquiry|requested|proposed|suggested)",
    r"(?:rsvp|confirm|decline|accept|reject)",
    r"(?:deadline|due|urgent|asap|important)",
]

# Response patterns — phrases that look like the user is answering something
_RESPONSE_PATTERNS = [
    r"(?:yes|no|okay|sure|go ahead|skip|cancel|ignore)",
    r"(?:don't|do not)\s+(?:reply|send|react|respond)",
    r"(?:just|only|also)\s+",
]


@dataclass
class SubIntent:
    """A single decomposed intent from a multi-intent utterance."""

    text: str
    intent_type: str = "unknown"  # 'query', 'command', 'approval', 'rejection', 'clarification'
    targets: list[str] = field(default_factory=list)  # entities/tools this targets
    negated: bool = False  # True if this is a "don't do X" instruction
    negates_target: str = ""  # what the negation applies to
    confidence: float = 1.0
    order: int = 0  # position in the original utterance


class MultiIntentDecomposer:
    """Split a single utterance into multiple parallel sub-intents.

    Uses deterministic sentence-level splitting, not the LLM, for reliability.
    Each sub-intent is classified by type and scope.
    """

    def __init__(self) -> None:
        self._split_pattern = re.compile(
            "|".join(f"({p})" for p in _SPLITTERS), re.IGNORECASE
        )
        self._negation_pattern = re.compile(
            "|".join(_NEGATION_PATTERNS), re.IGNORECASE
        )

    def decompose(self, utterance: str, context: DialogueContext | None = None) -> list[SubIntent]:
        """Split an utterance into sub-intents.

        Returns a list of SubIntent objects, each with its own text, type,
        targets, and negation status.
        """
        if not utterance or not utterance.strip():
            return []

        # Step 1: Split into clauses
        clauses = self._split_clauses(utterance)
        if len(clauses) <= 1:
            return [self._classify_clause(clauses[0] if clauses else utterance, 0, context)]

        # Step 2: Classify each clause
        intents = []
        for i, clause in enumerate(clauses):
            intent = self._classify_clause(clause, i, context)
            intents.append(intent)

        # Step 3: Link negations — if a clause says "don't reply to the mail",
        # the negation applies to the previous or associated clause
        self._link_negations(intents)

        return intents

    def _split_clauses(self, utterance: str) -> list[str]:
        """Split utterance into clauses using delimiter patterns."""
        # Try splitting on each pattern, take the finest split
        best_split = [utterance]
        for pattern in _SPLITTERS:
            parts = re.split(pattern, utterance, flags=re.IGNORECASE)
            parts = [p.strip().strip(",").strip() for p in parts if p and p.strip()]
            if len(parts) > len(best_split):
                best_split = parts
        return best_split

    def _classify_clause(self, clause: str, order: int, context: DialogueContext | None) -> SubIntent:
        """Classify a single clause into its intent type."""
        text = clause.strip()
        low = text.lower()

        # Detect negation
        negated = False
        negates_target = ""
        neg_match = self._negation_pattern.search(low)
        if neg_match:
            negated = True
            # Extract what's being negated (the rest of the clause after negation)
            neg_pos = neg_match.end()
            negates_target = text[neg_pos:].strip()

        # Classify intent type
        intent_type = "command"
        if any(re.search(p, low, re.IGNORECASE) for p in _RESPONSE_PATTERNS):
            if low in ("yes", "sure", "okay", "go ahead"):
                intent_type = "approval"
            elif low in ("no", "skip", "cancel", "ignore") or negated:
                intent_type = "rejection"
            else:
                intent_type = "command"
        elif any(re.search(p, low, re.IGNORECASE) for p in _ACTIONABILITY_SIGNALS):
            intent_type = "query"
        elif low.startswith(("what", "who", "where", "when", "how", "why", "which")):
            intent_type = "query"
        elif low.startswith(("tell me", "show me", "list", "read")):
            intent_type = "query"
        elif negated:
            intent_type = "rejection"
        else:
            intent_type = "command"

        # Extract entity references from context
        targets = self._extract_targets(text, context)

        return SubIntent(
            text=text,
            intent_type=intent_type,
            targets=targets,
            negated=negated,
            negates_target=negates_target,
            order=order,
        )

    def _extract_targets(self, text: str, context: DialogueContext | None) -> list[str]:
        """Extract entity/tool targets from a clause."""
        targets = []
        low = text.lower()

        # Common target patterns
        target_patterns = [
            (r"(?:reply|respond|send|message)\s+to\s+(\w+)", "person"),
            (r"(?:react|thumbs)\s+(?:up|down)?\s+(?:to|on)\s+(?:the\s+)?(\w+)", "email"),
            (r"(?:open|launch|start)\s+(.+?)(?:\s+and|\s*$)", "app"),
            (r"(?:check|read|see|get)\s+(.+?)(?:\s+and|\s*$)", "source"),
        ]

        for pattern, target_type in target_patterns:
            matches = re.findall(pattern, low)
            for match in matches:
                targets.append(match.strip())

        # If context exists, try to resolve references
        if context:
            for word in low.split():
                resolved = context.resolve_reference(word)
                if resolved and isinstance(resolved, Entity):
                    targets.append(resolved.name)

        return list(set(targets))  # deduplicate

    def _link_negations(self, intents: list[SubIntent]) -> None:
        """Link negation clauses to their targets.

        If one clause is "don't reply to the mail" and another is "tell Disha I'll
        be late", the negation should NOT bleed into the second clause.
        """
        for intent in intents:
            if intent.negated and intent.negates_target:
                # The negation is already scoped to this clause's target
                # No cross-clause bleeding by design
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Actionability Flagger
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ActionableItem:
    """An item from a read-only result that looks like it needs a response."""

    source: str  # 'email', 'message', 'notification', etc.
    sender: str
    summary: str
    suggested_action: str  # 'reply', 'react', 'forward', 'archive', etc.
    urgency: str = "medium"  # 'low', 'medium', 'high'
    confidence: float = 0.8


class ActionabilityFlagger:
    """Detect items in read-only results that look like they need a response.

    This is a Prepare-tier pass: it flags items as potentially actionable
    but NEVER drafts or sends anything. It surfaces the question; it never
    auto-acts.
    """

    def __init__(self) -> None:
        self._signal_pattern = re.compile(
            "|".join(_ACTIONABILITY_SIGNALS), re.IGNORECASE
        )

    def flag(self, results: list[dict[str, Any]], source_type: str = "email") -> list[ActionableItem]:
        """Scan read-only results for actionable items.

        Args:
            results: List of result dicts from tools (e.g. email digest items).
            source_type: The type of source ('email', 'message', 'notification').

        Returns:
            List of ActionableItem objects that the user should be asked about.
        """
        actionable = []
        for item in results:
            item_text = self._item_to_text(item)
            if self._looks_actionable(item_text):
                action = self._suggest_action(item_text, source_type)
                urgency = self._assess_urgency(item_text)
                sender = item.get("sender", item.get("from", item.get("name", "unknown")))
                summary = item.get("summary", item.get("snippet", item_text[:100]))

                actionable.append(ActionableItem(
                    source=source_type,
                    sender=str(sender),
                    summary=summary,
                    suggested_action=action,
                    urgency=urgency,
                ))
        return actionable

    def _item_to_text(self, item: dict[str, Any]) -> str:
        """Convert a result item to searchable text."""
        parts = []
        for key in ("subject", "summary", "snippet", "body", "text", "content"):
            if key in item and item[key]:
                parts.append(str(item[key]))
        return " ".join(parts)

    def _looks_actionable(self, text: str) -> bool:
        """Check if text contains signals that it needs a response."""
        return bool(self._signal_pattern.search(text))

    def _suggest_action(self, text: str, source_type: str) -> str:
        """Suggest what kind of response the item might need."""
        low = text.lower()
        if any(w in low for w in ("rsvp", "confirm", "accept", "decline")):
            return "respond"
        if any(w in low for w in ("question", "inquiry", "wondering", "asked")):
            return "reply"
        if any(w in low for w in ("urgent", "asap", "important", "deadline")):
            return "prioritize"
        if source_type == "message":
            return "reply"
        return "review"

    def _assess_urgency(self, text: str) -> str:
        """Assess urgency from text signals."""
        low = text.lower()
        if any(w in low for w in ("urgent", "asap", "important", "critical", "deadline")):
            return "high"
        if any(w in low for w in ("reminder", "follow up", "waiting")):
            return "medium"
        return "low"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Compound Command Splitter
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class SplitCommand:
    """A single instruction from a compound command."""

    text: str
    target: str = ""  # what/who this targets
    action: str = ""  # what to do
    negated: bool = False
    negates_target: str = ""  # what the negation applies to
    channel: str = ""  # which channel this goes through
    order: int = 0


class CompoundCommandSplitter:
    """Parse a single reply into multiple independent instructions.

    Handles: "Don't reply to the mail, just react with a thumbs up, and also
    tell Disha I'll be reaching in another hour."

    Key requirement: negation on one target must NOT bleed into another.
    """

    def __init__(self) -> None:
        self._splitter = MultiIntentDecomposer()

    def split(
        self, utterance: str, context: DialogueContext | None = None
    ) -> list[SplitCommand]:
        """Split a compound command into independent instructions."""
        intents = self._splitter.decompose(utterance, context)
        commands = []
        for intent in intents:
            cmd = SplitCommand(
                text=intent.text,
                target=", ".join(intent.targets) if intent.targets else "",
                action=self._infer_action(intent),
                negated=intent.negated,
                negates_target=intent.negates_target,
                order=intent.order,
            )
            commands.append(cmd)
        return commands

    def _infer_action(self, intent: SubIntent) -> str:
        """Infer the action from the intent text."""
        low = intent.text.lower()
        if any(w in low for w in ("reply", "respond", "send")):
            return "send"
        if any(w in low for w in ("react", "thumbs")):
            return "react"
        if any(w in low for w in ("tell", "say", "inform")):
            return "send"
        if any(w in low for w in ("check", "read", "see")):
            return "read"
        if any(w in low for w in ("open", "launch")):
            return "open"
        return "execute"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Response Composer
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ComposedResponse:
    """A merged, natural-language response from multiple tool results."""

    spoken: str  # what to say aloud
    items: list[dict[str, Any]] = field(default_factory=list)  # structured data
    actionables: list[ActionableItem] = field(default_factory=list)  # items needing response
    follow_up: str = ""  # suggested follow-up question


class ResponseComposer:
    """Merge multiple tool results into one natural spoken sentence.

    Instead of reading results as a concatenated list, compose them into
    a single coherent response that sounds like a person talking.
    """

    def compose(
        self,
        results: list[dict[str, Any]],
        context: DialogueContext | None = None,
        actionables: list[ActionableItem] | None = None,
    ) -> ComposedResponse:
        """Compose multiple results into a natural response."""
        if not results:
            return ComposedResponse(spoken="There's nothing new to report.")

        parts = []
        all_actionables = list(actionables or [])

        for result in results:
            spoken = self._result_to_spoken(result)
            if spoken:
                parts.append(spoken)

        # Merge into one natural sentence
        if len(parts) == 1:
            spoken = parts[0]
        elif len(parts) == 2:
            spoken = f"{parts[0]} and {parts[1].lower()}"
        else:
            spoken = ", ".join(parts[:-1]) + f", and {parts[-1].lower()}"

        # Add actionable items as a follow-up question
        follow_up = ""
        if all_actionables:
            if len(all_actionables) == 1:
                a = all_actionables[0]
                follow_up = f"Would you like to {a.suggested_action} to {a.sender}?"
            else:
                senders = ", ".join(a.sender for a in all_actionables[:3])
                follow_up = f"These look like they need responses: {senders}. What would you like to do?"

        return ComposedResponse(
            spoken=spoken,
            items=results,
            actionables=all_actionables,
            follow_up=follow_up,
        )

    def _result_to_spoken(self, result: dict[str, Any]) -> str:
        """Convert a single tool result to a spoken sentence fragment."""
        # Email result
        if "sender" in result and "subject" in result:
            urgency = result.get("urgency", "normal")
            prefix = "An important email" if urgency == "high" else "An email"
            sender = result["sender"]
            subject = result.get("subject", "")
            return f"{prefix} from {sender} about {subject}"

        # Message result
        if "sender" in result and "summary" in result:
            sender = result["sender"]
            summary = result["summary"]
            return f"{sender} sent a message: {summary}"

        # Calendar result
        if "title" in result and ("time" in result or "date" in result):
            title = result["title"]
            when = result.get("time", result.get("date", "soon"))
            return f"You have {title} {when}"

        # Generic result with summary
        if "summary" in result:
            return result["summary"]

        # Fallback
        if "text" in result:
            return result["text"][:200]

        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Call-Session Trust Envelope
# ═══════════════════════════════════════════════════════════════════════════════


class TrustLevel(str, Enum):
    """Trust level for a call session."""

    UNVERIFIED = "unverified"
    VOICEPRINT_VERIFIED = "voiceprint_verified"
    EXPLICITLY_CONFIRMED = "explicitly_confirmed"


@dataclass
class CallSession:
    """A verified call session with trust state."""

    session_id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}")
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    voiceprint_verified: bool = False
    started_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    verified_at: float | None = None

    def is_verified(self) -> bool:
        """True if voiceprint was verified at pickup."""
        return self.trust_level in (
            TrustLevel.VOICEPRINT_VERIFIED,
            TrustLevel.EXPLICITLY_CONFIRMED,
        )

    def can_execute(self) -> bool:
        """Execute-tier actions proceed on spoken 'yes' within a verified session."""
        return self.is_verified()

    def can_critical(self) -> bool:
        """Critical-tier actions ALWAYS need fresh explicit confirmation."""
        return False  # always requires explicit confirmation mid-call

    def touch(self) -> None:
        self.last_activity = float(time.monotonic())

    @property
    def session_duration(self) -> float:
        return time.monotonic() - self.started_at


class CallSessionTrust:
    """Manages trust state for a call session.

    Flow:
      1. Call starts → UNVERIFIED
      2. User answers → voiceprint verified → VOICEPRINT_VERIFIED
      3. Execute-tier: proceeds on spoken "yes" (verified session = trust)
      4. Critical-tier: ALWAYS requires fresh explicit confirmation
      5. Session timeout → UNVERIFIED (must re-verify)
    """

    def __init__(self, voiceprint_checker: Callable | None = None, timeout_seconds: float = 1800) -> None:
        """
        Args:
            voiceprint_checker: Callable that takes audio and returns (passed, reason).
            timeout_seconds: Session timeout (default 30 minutes).
        """
        self._voiceprint_checker = voiceprint_checker
        self._timeout_seconds = timeout_seconds
        self._session: CallSession | None = None

    def start_session(self) -> CallSession:
        """Start a new call session (unverified until voiceprint check)."""
        self._session = CallSession()
        logger.info("Call session started: %s", self._session.session_id)
        return self._session

    def verify_voiceprint(self, audio: Any, sample_rate: int = 16000) -> tuple[bool, str]:
        """Verify the user's voiceprint at call pickup.

        Returns (passed, reason). If passed, session is promoted to
        VOICEPRINT_VERIFIED and Execute-tier actions can proceed on spoken "yes".
        """
        if self._session is None:
            return False, "no active session"

        if self._voiceprint_checker is None:
            return False, "voiceprint checker not configured"

        passed, reason = self._voiceprint_checker(audio, sample_rate)
        if passed:
            self._session.trust_level = TrustLevel.VOICEPRINT_VERIFIED
            self._session.voiceprint_verified = True
            self._session.verified_at = time.monotonic()
            logger.info("Call session %s voiceprint verified", self._session.session_id)
        else:
            logger.warning("Call session %s voiceprint failed: %s", self._session.session_id, reason)

        return passed, reason

    def confirm_critical(self) -> bool:
        """Explicit confirmation for a Critical-tier action mid-call.

        Always required, even in a verified session. Returns True if confirmed.
        """
        if self._session is None:
            return False
        self._session.trust_level = TrustLevel.EXPLICITLY_CONFIRMED
        self._session.touch()
        return True

    def can_execute(self) -> bool:
        """Check if Execute-tier actions can proceed."""
        if self._session is None:
            return False
        if self._is_expired():
            self._expire()
            return False
        return self._session.can_execute()

    def can_critical(self) -> bool:
        """Check if Critical-tier actions can proceed (always needs explicit confirm)."""
        return False  # must call confirm_critical() first

    def end_session(self) -> None:
        """End the current call session."""
        if self._session:
            logger.info("Call session ended: %s (duration=%.1fs)", self._session.session_id, self._session.session_duration)
        self._session = None

    @property
    def session(self) -> CallSession | None:
        return self._session

    def _is_expired(self) -> bool:
        if self._session is None:
            return True
        return (time.monotonic() - self._session.last_activity) > self._timeout_seconds

    def _expire(self) -> None:
        if self._session:
            logger.warning("Call session %s expired", self._session.session_id)
        self._session = None


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Dialogue Orchestrator — the main coordinator
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class OrchestratorResult:
    """The output of processing one utterance through the dialogue orchestrator."""

    sub_intents: list[SubIntent] = field(default_factory=list)
    actionables: list[ActionableItem] = field(default_factory=list)
    split_commands: list[SplitCommand] = field(default_factory=list)
    composed_response: ComposedResponse | None = None
    requires_approval: bool = False
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    context_snapshot: dict[str, Any] = field(default_factory=dict)


class DialogueOrchestrator:
    """The main coordinator for dialogue processing.

    Sits between the voice layer and the Phase 2 task planner. Handles:
      - Multi-intent decomposition
      - Reference resolution via call context
      - Actionability flagging
      - Compound command splitting
      - Response composition
      - Trust envelope management

    Usage:
        orch = DialogueOrchestrator()
        session = orch.start_call()
        # ... voiceprint verified ...
        result = orch.process("Yes, go on, also tell me which mails I got")
        # result.sub_intents has 2 intents
        # result.composed_response has the merged spoken answer
    """

    def __init__(
        self,
        voiceprint_checker: Callable | None = None,
        actionability_flagger: ActionabilityFlagger | None = None,
    ) -> None:
        self.context = DialogueContext()
        self.decomposer = MultiIntentDecomposer()
        self.flagger = actionability_flagger or ActionabilityFlagger()
        self.splitter = CompoundCommandSplitter()
        self.composer = ResponseComposer()
        self.trust = CallSessionTrust(voiceprint_checker=voiceprint_checker)

    def start_call(self) -> CallSession:
        """Start a new call session."""
        self.context.clear()
        return self.trust.start_session()

    def process(
        self,
        utterance: str,
        tool_results: list[dict[str, Any]] | None = None,
        source_type: str = "email",
    ) -> OrchestratorResult:
        """Process one user utterance through the full dialogue pipeline.

        Returns an OrchestratorResult with decomposed intents, flagged
        actionables, split commands, and a composed response.
        """
        self.context.increment_turn()

        # 1. Decompose into sub-intents
        sub_intents = self.decomposer.decompose(utterance, self.context)

        # 2. Split compound commands
        split_commands = self.splitter.split(utterance, self.context)

        # 3. Flag actionables from any tool results
        actionables = []
        if tool_results:
            actionables = self.flagger.flag(tool_results, source_type)
            # Store results in context for reference resolution
            for result in tool_results:
                self.context.add_result(ToolResult(
                    tool=result.get("tool", "unknown"),
                    result=result,
                ))

        # 4. Track entities mentioned in the utterance
        self._extract_and_track_entities(utterance)

        # 5. Compose response if there are results
        composed = None
        if tool_results:
            composed = self.composer.compose(tool_results, self.context, actionables)

        # 6. Determine trust level
        trust_level = (
            self.trust.session.trust_level
            if self.trust.session
            else TrustLevel.UNVERIFIED
        )

        return OrchestratorResult(
            sub_intents=sub_intents,
            actionables=actionables,
            split_commands=split_commands,
            composed_response=composed,
            requires_approval=any(
                si.intent_type in ("command", "rejection") for si in sub_intents
            ),
            trust_level=trust_level,
            context_snapshot=self.context.snapshot(),
        )

    def _extract_and_track_entities(self, utterance: str) -> None:
        """Extract and track entities mentioned in the utterance."""
        low = utterance.lower()

        # Person names (simple heuristic — in production, use NER)
        person_patterns = [
            r"(?:to|from|with|tell|ask|reply to|message)\s+(\w+)",
            r"(\w+)\s+(?:sent|says|asked|wants)",
        ]
        for pattern in person_patterns:
            matches = re.findall(pattern, low)
            for name in matches:
                if len(name) > 1 and name not in ("the", "a", "an", "my", "your", "his", "her"):
                    self.context.add_entity(Entity(
                        name=name.capitalize(),
                        entity_type="person",
                    ))

        # Channel references
        channel_map = {
            "mail": "email",
            "email": "email",
            "whatsapp": "whatsapp",
            "message": "message",
            "text": "message",
            "sms": "message",
        }
        for word, channel in channel_map.items():
            if word in low:
                self.context.add_entity(Entity(
                    name=f"the {word}",
                    entity_type=channel,
                    channel=channel,
                ))

    def end_call(self) -> None:
        """End the current call session."""
        self.trust.end_session()
        self.context.clear()
