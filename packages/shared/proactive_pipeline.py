"""
Proactive Call Pipeline — the brain that decides to call the user.

Flow:
    1. Check DigestSchedule.should_trigger()
    2. Fetch items from connectors (Gmail, WhatsApp, etc.)
    3. Compose ProactiveDigest with DigestItems
    4. Push RingEvent to phone via WebSocket hub
    5. Handle call lifecycle: ring → accept → speak digest → listen → respond
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parseaddr
from enum import Enum
from typing import Any, Callable, Optional

from .call_experience import (
    CallExperience,
    CallState,
    DigestItem,
    DigestSchedule,
    ProactiveDigest,
    RingEvent,
    RingSource,
)
from .call_triggers import CallTriggerStore, TriggerMatch
from .contacts import Contact, ContactBook, relationship_phrases
from .dialogue import DialogueOrchestrator, SplitCommand
from .email_connector import GmailConnector
from .permission_engine import PermissionEngine

logger = logging.getLogger(__name__)

# Explicitly named demo fixture used only by the scripted driving scenario.
# Production calls are built from whatever the connectors actually return.
_DEMO_DRIVING_ITEMS: list[dict[str, Any]] = [
    {
        "item_type": "task",
        "summary": "Project LUME completed Phase 4",
        "source": "orchestrator",
        "priority": "normal",
        "actionable": True,
    },
    {
        "item_type": "email",
        "summary": "From RazorClub: Tomorrow will be an important meeting on next hires",
        "source": "gmail",
        "priority": "high",
        "actionable": True,
        "sender": "RazorClub",
        "subject": "Tomorrow will be an important meeting on next hires",
    },
    {
        "item_type": "message",
        "summary": "From Disha (wife): When are you reaching home?",
        "source": "whatsapp",
        "priority": "high",
        "actionable": True,
        "sender": "Disha",
        "subject": "When are you reaching home?",
        "channel": "whatsapp",
    },
]

_DEMO_CONTACTS = [
    Contact(name="Shravan G", email="shravan.g@example.com", phone="+919876543210"),
    Contact(
        name="Shravan GK",
        email="shravan.gk@sceptix.com",
        phone="+919876543211",
        aliases=["sceptix"],
    ),
    Contact(
        name="Disha",
        phone="+919876500001",
        relationship="wife",
        aliases=["wife"],
    ),
]

#: Gmail has no emoji reactions. Only the two spoken reactions with an honest,
#: reversible label equivalent are mapped; anything else is refused rather than
#: inventing a mailbox change the user did not ask for. The connector's own
#: vocabulary (acknowledge/star/unstar/mark_read/mark_unread) stays authoritative.
_GMAIL_REACTION_FOR_EMOJI: dict[str, str] = {
    "👍": "acknowledge",  # star + mark read
    "❤️": "star",
}

#: Bare confirmations/cancellations a phone can speak. Module-level so the
#: gateway resolves a task approval with the exact same vocabulary the pipeline
#: uses for prepared actions — no second, drifting list.
APPROVAL_UTTERANCES = frozenset(
    {"yes", "go on", "approve", "do it", "send it", "confirm"}
)
REJECTION_UTTERANCES = frozenset({"no don't", "cancel", "reject", "don't"})


class CallRole(Enum):
    LAPTOP = "laptop"  # the server side
    PHONE = "phone"  # the client side


class DigestStatus(Enum):
    PENDING = "pending"
    SPOKEN = "spoken"
    AWAITING_RESPONSE = "awaiting_response"
    RESPONSE_RECEIVED = "response_received"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DigestTurn:
    """One turn in the digest conversation."""

    role: str  # "laptop" or "phone"
    text: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class CallSession:
    """A single call session — tracks the full lifecycle."""

    call_id: str
    source: RingSource
    status: DigestStatus = DigestStatus.PENDING
    digest: Optional[ProactiveDigest] = None
    turns: list[DigestTurn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    spoken_text: str = ""
    pending_actions: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def add_turn(self, role: str, text: str, **metadata: Any) -> DigestTurn:
        turn = DigestTurn(role=role, text=text, metadata=metadata)
        self.turns.append(turn)
        return turn

    def get_user_intent(self) -> str:
        """Get the last phone turn as a raw utterance."""
        for turn in reversed(self.turns):
            if turn.role == "phone":
                return turn.text
        return ""


class ProactiveCallPipeline:
    """
    Manages the full proactive call lifecycle.

    Usage:
        pipeline = ProactiveCallPipeline(hub, gmail, contacts)
        # In a background loop:
        await pipeline.tick()  # checks triggers, starts calls
        # When phone sends a message:
        await pipeline.handle_phone_message(call_id, utterance)
    """

    def __init__(
        self,
        hub: Any,  # WebSocketHub
        gmail: Optional[GmailConnector] = None,
        contacts: Optional[ContactBook] = None,
        on_action: Optional[Callable] = None,
        dialogue: Optional[DialogueOrchestrator] = None,
        seed_demo_contacts: bool = False,
        trigger_store: Optional[CallTriggerStore] = None,
        digest_fetcher: Optional[Callable] = None,
        contact_loader: Optional[Callable] = None,
    ):
        self.hub = hub
        self.gmail = gmail or GmailConnector()
        self.contacts = contacts or ContactBook()
        if seed_demo_contacts and not self.contacts.list_all():
            for contact in _DEMO_CONTACTS:
                self.contacts.add(contact)
        self.on_action = on_action  # callback for executing approved actions
        self.dialogue = dialogue or DialogueOrchestrator()
        # Optional: user-defined call triggers. None means "no custom triggers"
        # rather than silently creating a file in the data dir.
        self.trigger_store = trigger_store
        self.digest_fetcher = digest_fetcher
        self.contact_loader = contact_loader

        self._sessions: dict[str, CallSession] = {}
        self._schedule = DigestSchedule()
        self._call_experience = CallExperience()

    # ── tick: called periodically to check triggers ───────────────────────

    async def tick(
        self, completed_tasks: Optional[Iterable[dict[str, Any]]] = None
    ) -> Optional[str]:
        """Check user triggers, then the daily digest schedule.

        ``completed_tasks`` is the task-runner snapshot used by
        ``task_completed`` triggers. A trigger is marked fired only after the
        call is actually created, so a failed call retries on the next tick.
        """
        if self.trigger_store is not None:
            matches = self.trigger_store.due(completed_tasks=completed_tasks)
            if matches:
                match = matches[0]
                call_id = str(uuid.uuid4())[:8]
                logger.info(
                    "Call trigger %s fired (%s), starting call %s",
                    match.trigger.trigger_id,
                    match.reason,
                    call_id,
                )
                extra_items = (
                    [self._task_completed_item(match)] if match.task_id else None
                )
                await self._start_call(
                    call_id,
                    reason=match.reason,
                    extra_items=extra_items,
                    record_schedule=False,
                )
                self.trigger_store.mark_fired(
                    match.trigger.trigger_id, task_id=match.task_id
                )
                return call_id

        if not self._schedule.should_trigger():
            return None

        call_id = str(uuid.uuid4())[:8]
        logger.info("Digest triggered, starting call %s", call_id)

        await self._start_call(call_id, driving_scenario=False)
        return call_id

    @staticmethod
    def _task_completed_item(match: TriggerMatch) -> DigestItem:
        """Describe a finished task in the digest the user hears."""
        request = match.task_request or "Your task"
        return DigestItem(
            item_type="task",
            summary=f"{request} finished",
            source="task_runner",
            priority="normal",
            actionable=False,
            reference_id=match.task_id,
        )

    async def _start_call(
        self,
        call_id: str,
        driving_scenario: bool = False,
        *,
        reason: Optional[str] = None,
        extra_items: Optional[list[DigestItem]] = None,
        record_schedule: bool = True,
    ) -> CallSession:
        """Fetch items, compose digest, push ring to phone."""
        # Demo-only scenario must be asked for explicitly; production calls use
        # whichever connectors are actually connected.
        if driving_scenario and not self.contacts.list_all():
            for contact in _DEMO_CONTACTS:
                self.contacts.add(contact)
        elif not driving_scenario and self.contact_loader is not None and not self.contacts.list_all():
            try:
                loaded = self.contact_loader()
                if inspect.isawaitable(loaded):
                    loaded = await loaded
                for contact in loaded or []:
                    self.contacts.add(contact)
            except Exception as exc:
                logger.warning("Failed to load contacts: %s", exc)

        # 1. Fetch items from connectors
        items = await self._fetch_digest_items(driving_scenario=driving_scenario)
        if extra_items:
            items = [*extra_items, *items]

        # 2. Compose digest
        digest = ProactiveDigest(call_id=call_id, items=items)

        # 3. Compose the greeting from the real items via the dialogue layer.
        #    No canned names or project scripts live in this path.
        self.dialogue.start_call()
        spoken_text = self._compose_digest_speech(items)
        # Expose the call-scoped trust envelope to the permission engine so a
        # voiceprint-verified Execute grant at pickup is honoured mid-call.
        PermissionEngine.set_call_trust(self.dialogue.trust)

        # 4. Create session
        session = CallSession(
            call_id=call_id,
            source=RingSource.PROACTIVE,
            digest=digest,
            spoken_text=spoken_text,
        )
        self._sessions[call_id] = session

        # 5. Start call experience
        ring = RingEvent(
            call_id=call_id,
            source=RingSource.PROACTIVE,
            caller_name="Vioris",
            reason=reason or f"Daily digest — {len(items)} items",
        )
        self._call_experience.start_ring(ring)

        # 6. Push ring to all authenticated phones
        await self.hub.send_to_phones(
            {
                "type": "ring",
                "payload": {
                    "call_id": call_id,
                    "source": "proactive",
                    "caller_name": "Vioris",
                    "reason": ring.reason,
                    "digest_preview": spoken_text[:200],
                },
            }
        )

        # A user trigger must not consume the daily digest slot.
        if record_schedule:
            self._schedule.record_trigger()
        logger.info("Ring pushed for call %s with %d items", call_id, len(items))
        return session

    async def _fetch_digest_items(self, driving_scenario: bool = False) -> list[DigestItem]:
        """Gather items from all connected connectors."""
        items: list[DigestItem] = []

        # Gmail unread
        if self.gmail and self.gmail.is_connected():
            try:
                unread = self.gmail.list_messages(query="is:unread", max_results=10)
                for msg in unread:
                    sender = msg.from_addr.split("<")[0].strip()
                    items.append(
                        DigestItem(
                            item_type="email",
                            summary=f"From {sender}: {msg.subject}",
                            source="gmail",
                            priority="high" if not msg.is_read else "normal",
                            actionable=True,
                            sender=sender,
                            subject=msg.subject,
                            channel="email",
                            reference_id=msg.id,
                            sender_email=parseaddr(msg.from_addr)[1],
                            thread_id=msg.thread_id,
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to fetch Gmail: %s", exc)

        if not items and not driving_scenario and self.digest_fetcher is not None:
            try:
                fetched = self.digest_fetcher()
                if inspect.isawaitable(fetched):
                    fetched = await fetched
                if fetched:
                    items.extend(fetched)
            except Exception as exc:
                logger.warning("Failed to fetch connector digest: %s", exc)

        if not items and driving_scenario:
            items.extend(DigestItem(**item) for item in _DEMO_DRIVING_ITEMS)

        return items

    def _compose_digest_speech(self, items: list[DigestItem]) -> str:
        """Compose a spoken greeting from the actual digest items.

        Uses the dialogue layer's composer/actionability flagger so the
        greeting reflects the real senders and subjects, not a canned script.
        """
        if not items:
            return "Nothing new right now."

        tool_results = [self._item_to_tool_result(item) for item in items]
        actionables = self.dialogue.flagger.flag(tool_results, source_type="email")
        composed = self.dialogue.composer.compose(tool_results, actionables=actionables)

        if composed.follow_up:
            return f"{composed.spoken} {composed.follow_up}"
        return composed.spoken

    @staticmethod
    def _item_to_tool_result(item: DigestItem) -> dict[str, Any]:
        """Shape a digest item for the dialogue response composer."""
        result: dict[str, Any] = {
            "tool": item.source or item.item_type,
            "summary": item.summary,
            "urgency": item.priority,
        }
        if item.item_type == "email":
            result["sender"] = item.sender or ProactiveCallPipeline._sender_from_summary(item.summary)
            result["subject"] = item.subject or ProactiveCallPipeline._subject_from_summary(item.summary)
        elif item.item_type in ("message", "sms", "whatsapp"):
            result["sender"] = item.sender or ProactiveCallPipeline._sender_from_summary(item.summary)
            result["summary"] = item.subject or item.summary
        return result

    @staticmethod
    def _sender_from_summary(summary: str) -> str:
        if summary.lower().startswith("from "):
            return summary[5:].split(":", 1)[0].strip()
        return ""

    @staticmethod
    def _subject_from_summary(summary: str) -> str:
        if summary.lower().startswith("from ") and ":" in summary:
            return summary.split(":", 1)[1].strip()
        return summary

    # ── handle phone accepting the call ───────────────────────────────────

    async def handle_call_accept(self, call_id: str) -> Optional[str]:
        """Phone accepted the ring. Return the spoken digest text."""
        session = self._sessions.get(call_id)
        if not session:
            return None

        session.status = DigestStatus.SPOKEN
        self._call_experience.accept_call()
        self._call_experience.start_active()

        session.add_turn("laptop", session.spoken_text, type="digest")

        return session.spoken_text

    async def handle_call_end(self, call_id: str) -> bool:
        """End a call from the phone. Clears trust and call-scoped context."""
        session = self._sessions.get(call_id)
        if not session:
            return False
        session.status = DigestStatus.COMPLETED
        session.ended_at = time.time()
        self._call_experience.end_call()
        self.dialogue.end_call()
        PermissionEngine.set_call_trust(None)
        return True

    # ── handle phone sending a voice message ──────────────────────────────

    async def handle_phone_message(
        self, call_id: str, utterance: str
    ) -> dict[str, Any]:
        """
        Process a voice utterance from the phone.

        Returns:
            {
                "response_text": "natural language response",
                "action": None or {"type": "approve"/"reject"/"execute", ...},
                "digest_status": "completed" | "awaiting_response" | ...
            }
        """
        session = self._sessions.get(call_id)
        if not session:
            return {"response_text": "Call not found", "action": None}

        session.add_turn("phone", utterance)

        # Parse the utterance
        result = self._process_utterance(session, utterance)

        # Dispatch confirmed actions through the wired action handler (never
        # silently bypassing the permission/approval engine). If no handler is
        # wired, say so plainly — a spoken "yes" must never imply delivery.
        action = result.get("action")
        if isinstance(action, dict) and action.get("type") == "execute":
            result["queued"] = False
            if self.on_action is not None:
                try:
                    outcome = self.on_action(call_id, action.get("action") or action)
                    if inspect.isawaitable(outcome):
                        outcome = await outcome
                    if isinstance(outcome, dict) and outcome.get("note"):
                        result["response_text"] = (
                            f"{result['response_text']} {outcome['note']}"
                        ).strip()
                        result["task_id"] = outcome.get("task_id")
                        result["queued"] = outcome.get("queued", True)
                        if outcome.get("approval_id"):
                            result["approval_id"] = outcome["approval_id"]
                            session.metadata["pending_call_approval"] = {
                                "task_id": outcome.get("task_id"),
                                "approval_id": outcome["approval_id"],
                            }
                except Exception as exc:
                    logger.warning("Call on_action callback failed: %s", exc)
                    result["response_text"] = (
                        f"{result['response_text']} Could not queue that step: {exc}"
                    ).strip()
            else:
                result["response_text"] = (
                    f"{result['response_text']} Nothing has been sent or executed — "
                    "a transport and approval handler are not connected yet."
                ).strip()
                logger.warning(
                    "Call %s confirmed an action but no on_action handler is wired", call_id
                )

        # Send response back to phone
        if result["response_text"]:
            session.add_turn("laptop", result["response_text"], type="response")
            await self.hub.send_to_phones(
                {
                    "type": "voice_response",
                    "payload": {
                        "call_id": call_id,
                        "text": result["response_text"],
                        "action": result.get("action"),
                    },
                }
            )

        return result

    def _process_utterance(
        self, session: CallSession, utterance: str
    ) -> dict[str, Any]:
        """Route utterance to the right handler based on context."""
        lower = utterance.lower().strip()

        # Let the dialogue layer track entities/context and split compound
        # instructions. The domain handlers below decide what to prepare; the
        # orchestrator ensures multi-clause replies are scoped correctly.
        dialogue_result = self.dialogue.process(utterance)
        session.metadata["dialogue_intents"] = [
            {
                "text": intent.text,
                "intent_type": intent.intent_type,
                "negated": intent.negated,
            }
            for intent in dialogue_result.sub_intents
        ]

        # ── End the call ──
        if (
            any(w in lower for w in ("bye", "goodbye", "end call", "hang up"))
            or lower.strip(".,! ") in (
                "stop", "thanks bye", "no that's all", "no thats all",
                "that's all", "thats all", "nothing", "nothing else",
                "no thanks", "all good", "no that is all",
            )
        ):
            session.status = DigestStatus.COMPLETED
            session.ended_at = time.time()
            self._call_experience.end_call()
            self.dialogue.end_call()
            PermissionEngine.set_call_trust(None)
            return {
                "response_text": "Talk to you later.",
                "action": None,
                "digest_status": "completed",
            }

        # ── Active disambiguation pending ──
        disambig = session.metadata.get("disambiguation")
        if disambig and disambig.get("type") == "contact_disambiguation":
            candidates = disambig.get("candidates", [])
            matched_candidate = None
            words = lower.split()
            # Sort by length descending so longer/more specific names match first (e.g. Shravan GK before Shravan G)
            sorted_cands = sorted(candidates, key=lambda c: len(c.name), reverse=True)
            for cand in sorted_cands:
                cand_lower = cand.name.lower()
                cand_parts = cand_lower.split()
                if cand_lower == lower:
                    matched_candidate = cand
                    break
                if cand_lower in lower:
                    matched_candidate = cand
                    break
                if any(p == lower or p in words for p in cand_parts[1:]):
                    matched_candidate = cand
                    break
                if any(alias.lower() in lower for alias in cand.aliases):
                    matched_candidate = cand
                    break

            if matched_candidate:
                session.metadata.pop("disambiguation", None)
                intent = disambig.get("intent", "send_mail")
                message = disambig.get("message") or "Hi"
                if intent == "send_mail":
                    action_payload = {
                        "type": "send_mail",
                        "recipient": matched_candidate.name,
                        "email": matched_candidate.email,
                        "body": message,
                        "status": "not_sent",
                        "description": f"prepared mail to {matched_candidate.name} ({matched_candidate.email}): '{message}' — not sent",
                    }
                    session.metadata.setdefault("prepared_emails", []).append(action_payload)
                    session.status = DigestStatus.RESPONSE_RECEIVED
                    return {
                        "response_text": f"I've prepared a mail to {matched_candidate.name} saying '{message}'. It has not been sent — no mail transport is configured. Anything else?",
                        "action": action_payload,
                        "digest_status": "awaiting_response",
                    }

        # ── Reply to an email in the digest ──
        # Runs before the send-mail parser: "reply to Disha's email saying X"
        # is a reply in an existing thread, not a brand-new mail. Only explicit
        # email/mail wording routes here; a bare "reply to Disha" keeps the
        # existing messaging behavior rather than guessing a channel.
        reply_intent = self._extract_reply_intent(utterance)
        if reply_intent:
            target = self._find_email_target(session, reply_intent["who"])
            who = reply_intent["who"] or "the sender"
            if target is None:
                return {
                    "response_text": (
                        f"I don't see an email from {who} in this digest. "
                        "Nothing was prepared."
                    ),
                    "action": None,
                    "digest_status": "awaiting_response",
                }
            if not target.thread_id or not target.sender_email or not target.subject:
                return {
                    "response_text": (
                        "I can't reply to that email because its thread id, "
                        "return address, or subject is missing. Nothing was prepared."
                    ),
                    "action": None,
                    "digest_status": "awaiting_response",
                }
            subject = target.subject
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            action = {
                "type": "reply",
                "thread_id": target.thread_id,
                "message_id": target.reference_id,
                "to": target.sender_email,
                "subject": subject,
                "body": reply_intent["body"],
                "status": "not_sent",
                "description": (
                    f"reply to {target.sender or who}: '{reply_intent['body']}' — not sent"
                ),
            }
            session.pending_actions.append(action)
            return {
                "response_text": (
                    f"I've prepared a reply to {target.sender or who}: "
                    f"'{reply_intent['body']}'. It has not been sent — should I send it?"
                ),
                "action": None,
                "digest_status": "awaiting_response",
            }

        # ── Send mail intent with contact resolution & disambiguation ──
        mail_intent = self._extract_mail_intent(utterance)
        if mail_intent:
            recipient_query = mail_intent["recipient"]
            body = mail_intent.get("body") or "Hi"
            matches = self.contacts.find(recipient_query, threshold=0.5)

            if len(matches) > 1:
                best_score = matches[0].matches(recipient_query)
                second_score = matches[1].matches(recipient_query)
                if best_score > second_score and best_score >= 0.95:
                    chosen = matches[0]
                    action_payload = {
                        "type": "send_mail",
                        "recipient": chosen.name,
                        "email": chosen.email,
                        "body": body,
                        "status": "not_sent",
                        "description": f"prepared mail to {chosen.name} ({chosen.email}): '{body}' — not sent",
                    }
                    session.metadata.setdefault("prepared_emails", []).append(action_payload)
                    session.status = DigestStatus.RESPONSE_RECEIVED
                    return {
                        "response_text": f"I've prepared a mail to {chosen.name} saying '{body}'. It has not been sent — no mail transport is configured. Anything else?",
                        "action": action_payload,
                        "digest_status": "awaiting_response",
                    }
                else:
                    names_str = " or ".join(c.name for c in matches)
                    session.metadata["disambiguation"] = {
                        "type": "contact_disambiguation",
                        "intent": "send_mail",
                        "candidates": matches,
                        "message": body,
                        "recipient_query": recipient_query,
                    }
                    return {
                        "response_text": f"Which {recipient_query.capitalize()}? {names_str}?",
                        "action": None,
                        "digest_status": "awaiting_response",
                    }
            elif len(matches) == 1:
                chosen = matches[0]
                action_payload = {
                    "type": "send_mail",
                    "recipient": chosen.name,
                    "email": chosen.email,
                    "body": body,
                    "status": "not_sent",
                    "description": f"prepared mail to {chosen.name} ({chosen.email}): '{body}' — not sent",
                }
                session.metadata.setdefault("prepared_emails", []).append(action_payload)
                session.status = DigestStatus.RESPONSE_RECEIVED
                return {
                    "response_text": f"I've prepared a mail to {chosen.name} saying '{body}'. It has not been sent — no mail transport is configured. Anything else?",
                    "action": action_payload,
                    "digest_status": "awaiting_response",
                }
            else:
                return {
                    "response_text": f"I couldn't find {recipient_query} in your contacts. What email address should I send it to?",
                    "action": None,
                    "digest_status": "awaiting_response",
                }

        # ── Standalone Open Mail ──
        if lower in ("open mail", "open gmail", "check mail", "check inbox", "my mails", "mails"):
            return {
                "response_text": self._describe_mail(session),
                "action": {"type": "open_app", "target": "gmail"},
                "digest_status": "awaiting_response",
            }

        # ── Compound command ──
        # Decompose the reply into independent instructions, then prepare each
        # resolvable action against the current digest/contact book. If fewer
        # than two actions resolve, fall through to the single-intent handlers
        # so we never invent a target the user did not mention.
        compound_actions = self._prepare_compound_actions(
            session, dialogue_result.split_commands
        )
        if len(compound_actions) >= 2:
            targets = ", ".join(
                str(action.get("target") or action.get("recipient") or "unknown")
                for action in compound_actions
            )
            session.metadata["last_prepared"] = compound_actions
            session.status = DigestStatus.RESPONSE_RECEIVED
            return {
                "response_text": (
                    f"I've prepared {len(compound_actions)} actions, but nothing was "
                    f"delivered: no transport is linked for {targets}. Want me to "
                    "queue them once a transport is connected?"
                ),
                "action": {"type": "compound_prepare", "actions": compound_actions},
                "digest_status": "awaiting_response",
            }

        # ── Multi-intent query: project continuation + mail + messages ──
        has_continue = any(w in lower for w in ("go on", "continue", "yes, go on", "yes go on", "start next", "next phase"))
        has_mail_query = any(w in lower for w in ("mail", "email", "inbox"))
        has_text_query = any(w in lower for w in ("text", "message", "sms", "whatsapp"))

        if (has_mail_query and has_text_query) or (has_continue and (has_mail_query or has_text_query)):
            return self._answer_inbox_query(
                session, include_continue=has_continue, include_mail=has_mail_query
            )

        # ── Skip / dismiss items ──
        if lower in ("skip", "next", "dismiss", "ignore", "no"):
            remaining = self._get_remaining_items(session)
            if remaining:
                return {
                    "response_text": f"OK, skipping. {len(remaining)} items left.",
                    "action": None,
                    "digest_status": "awaiting_response",
                }
            return {
                "response_text": "That's all for now. Talk to you later.",
                "action": None,
                "digest_status": "completed",
            }

        # ── Approve / confirm action ──
        if lower in APPROVAL_UTTERANCES:
            if session.pending_actions:
                action = session.pending_actions.pop(0)
                session.status = DigestStatus.RESPONSE_RECEIVED
                return {
                    "response_text": f"OK, {action.get('description', 'done')}.",
                    "action": {"type": "execute", "action": action},
                    "digest_status": "response_received",
                }
            return {
                "response_text": "Nothing pending to approve.",
                "action": None,
                "digest_status": "awaiting_response",
            }

        # ── Reject / cancel ──
        if lower in REJECTION_UTTERANCES:
            if session.pending_actions:
                session.pending_actions.pop(0)
                return {
                    "response_text": "OK, cancelled.",
                    "action": None,
                    "digest_status": "awaiting_response",
                }
            return {
                "response_text": "Nothing to cancel.",
                "action": None,
                "digest_status": "awaiting_response",
            }

        # ── Read more details ──
        if lower in ("tell me more", "more details", "read it", "open"):
            for item in session.digest.items if session.digest else []:
                if item.actionable:
                    return {
                        "response_text": f"{item.item_type}: {item.summary}",
                        "action": None,
                        "digest_status": "awaiting_response",
                    }
            return {
                "response_text": "That's all the details I have.",
                "action": None,
                "digest_status": "awaiting_response",
            }

        # ── Contact-specific: "tell disha..." ──
        if "tell" in lower or "reply" in lower or "message" in lower:
            contact_name = self._extract_contact(lower)
            if contact_name:
                contact = self.contacts.best_match(contact_name)
                message = (
                    self._message_for_contact(lower, contact)
                    if contact
                    else self._extract_message_after(lower)
                )
                if message:
                    session.pending_actions.append(
                        {
                            "type": "message",
                            "contact": contact.name if contact else contact_name,
                            "message": message,
                            "description": f"message to {contact.name if contact else contact_name}",
                        }
                    )
                    return {
                        "response_text": f"Got it. I'll tell {contact.name if contact else contact_name}: {message}. Should I send it?",
                        "action": None,
                        "digest_status": "awaiting_response",
                    }
                return {
                    "response_text": f"What should I tell {contact_name}?",
                    "action": None,
                    "digest_status": "awaiting_response",
                }

        # ── React to email ──
        if "react" in lower or any(emoji in lower for emoji in ("👍", "❤️", "😄")):
            reaction = self._extract_reaction(lower)
            if not reaction:
                return {
                    "response_text": "What reaction?",
                    "action": None,
                    "digest_status": "awaiting_response",
                }
            target = self._first_digest_item(session, "email")
            target_name = (
                (target.sender if target and target.sender else "")
                or (self._sender_from_summary(target.summary) if target else "")
                or "the email"
            )
            message_id = (target.reference_id if target else "") or ""
            mail_reaction = _GMAIL_REACTION_FOR_EMOJI.get(reaction)
            if not message_id:
                return {
                    "response_text": (
                        f"I don't have a message id for {target_name}'s mail, so I "
                        "can't apply a reaction. Nothing was changed."
                    ),
                    "action": None,
                    "digest_status": "awaiting_response",
                }
            if not mail_reaction:
                return {
                    "response_text": (
                        f"Gmail can't send a {reaction} reaction — it can only star, "
                        "unstar, or mark a message read. Nothing was changed."
                    ),
                    "action": None,
                    "digest_status": "awaiting_response",
                }
            session.pending_actions.append(
                {
                    "type": "react",
                    "reaction": mail_reaction,
                    "target": target_name,
                    "message_id": message_id,
                    "status": "not_executed",
                    "description": f"{mail_reaction} on {target_name}'s mail",
                }
            )
            return {
                "response_text": (
                    f"Gmail has no emoji reactions, so I've prepared '{mail_reaction}' "
                    f"on {target_name}'s mail instead. Nothing has been changed — "
                    "should I apply it?"
                ),
                "action": None,
                "digest_status": "awaiting_response",
            }

        # ── General query: fall through ──
        remaining = self._get_remaining_items(session)
        if remaining:
            return {
                "response_text": (
                    f"You said: {utterance}. "
                    f"There are still {len(remaining)} items. "
                    "Want to hear the next one?"
                ),
                "action": None,
                "digest_status": "awaiting_response",
            }

        return {
            "response_text": f"You said: {utterance}. Anything else?",
            "action": None,
            "digest_status": "awaiting_response",
        }

    # ── helpers ───────────────────────────────────────────────────────────

    def _get_remaining_items(self, session: CallSession) -> list[DigestItem]:
        """Get items not yet addressed."""
        if not session.digest:
            return []
        spoken_count = len(
            [t for t in session.turns if t.role == "laptop" and t.metadata.get("type") == "digest"]
        )
        return session.digest.items[spoken_count:]

    # ── digest-driven responses ───────────────────────────────────────────

    def _digest_items(self, session: CallSession) -> list[DigestItem]:
        return session.digest.items if session.digest else []

    def _items_of_type(self, session: CallSession, *item_types: str) -> list[DigestItem]:
        wanted = set(item_types)
        return [item for item in self._digest_items(session) if item.item_type in wanted]

    def _first_digest_item(self, session: CallSession, item_type: str) -> Optional[DigestItem]:
        for item in self._digest_items(session):
            if item.item_type == item_type:
                return item
        return None

    @staticmethod
    def _join_summaries(items: list[DigestItem], limit: int = 3) -> str:
        summaries = [item.summary for item in items[:limit]]
        text = "; ".join(summaries)
        if len(items) > limit:
            text += f"; and {len(items) - limit} more"
        return text

    @staticmethod
    def _as_sentence(text: str) -> str:
        text = text.strip()
        if text and text[-1] not in ".!?":
            return text + "."
        return text

    def _describe_mail(self, session: CallSession) -> str:
        emails = self._items_of_type(session, "email")
        if not emails:
            return "You have no unread mail right now. What would you like to do?"
        noun = "email" if len(emails) == 1 else "emails"
        body = f"You have {len(emails)} unread {noun}: {self._join_summaries(emails)}"
        return f"{self._as_sentence(body)} What would you like to do?"

    def _answer_inbox_query(
        self,
        session: CallSession,
        *,
        include_continue: bool,
        include_mail: bool,
    ) -> dict[str, Any]:
        """Answer a mail/message query using the digest, not a canned script."""
        emails = self._items_of_type(session, "email")
        messages = self._items_of_type(session, "message", "sms", "whatsapp")
        tasks = self._items_of_type(session, "task")

        if include_continue:
            session.pending_actions.clear()
            if tasks:
                task = tasks[0]
                session.pending_actions.append(
                    {
                        "type": "continue_task",
                        "task": task.summary,
                        "description": f"continue {task.summary}",
                    }
                )

        parts: list[str] = []
        if include_mail:
            if emails:
                parts.append(f"You have {len(emails)} unread email(s): {self._join_summaries(emails)}")
            else:
                parts.append("You have no unread mail")
        if messages:
            parts.append(f"Messages: {self._join_summaries(messages)}")

        if not parts:
            return {
                "response_text": "I don't see any unread mail or messages right now. What would you like to do?",
                "action": None,
                "digest_status": "awaiting_response",
            }

        session.metadata["awaiting_decision"] = bool(session.pending_actions)
        return {
            "response_text": f"{self._as_sentence('. '.join(parts))} What would you like to do?",
            "action": None,
            "digest_status": "awaiting_response",
        }

    # ── compound command preparation ──────────────────────────────────────

    def _prepare_compound_actions(
        self, session: CallSession, commands: list[SplitCommand]
    ) -> list[dict[str, Any]]:
        """Prepare each independent instruction without executing anything.

        Targets come from the digest (for mail reactions) and the contact book
        (for messages). Unresolvable clauses are dropped rather than filled in
        with a demo name.
        """
        actions: list[dict[str, Any]] = []
        for command in commands:
            if command.negated:
                continue
            text = command.text
            low = text.lower()

            if command.action == "react":
                email = self._first_digest_item(session, "email")
                reaction = self._extract_reaction(low)
                if not email or not reaction:
                    continue
                target_name = (
                    email.sender or self._sender_from_summary(email.summary) or "the email"
                )
                actions.append(
                    {
                        "type": "react_email",
                        "target": target_name,
                        "reference_id": email.reference_id,
                        "reaction": reaction,
                        "status": "not_executed",
                        "description": (
                            f"prepared {reaction} reaction to {target_name}'s mail "
                            "— not executed (no mail write transport linked)"
                        ),
                    }
                )
                continue

            if command.action == "send":
                contact = self._resolve_contact_from_text(text)
                if not contact:
                    continue
                message = self._message_for_contact(text, contact)
                if not message:
                    continue
                channel = "email" if contact.email and not contact.phone else "sms" if contact.phone else ""
                actions.append(
                    {
                        "type": "send_message",
                        "target": contact.name,
                        "channel": channel,
                        "message": message,
                        "status": "not_sent",
                        "description": (
                            f"prepared message to {contact.name}: '{message}' "
                            "— not sent (no transport linked)"
                        ),
                    }
                )
        return actions

    def _resolve_contact_from_text(self, text: str) -> Optional[Contact]:
        name = self._extract_contact(text.lower())
        if not name:
            return None
        matches = self.contacts.find(name, threshold=0.5)
        return matches[0] if matches else None

    @staticmethod
    def _message_for_contact(text: str, contact: Contact) -> Optional[str]:
        """Pull the message body that follows a name, alias or relationship."""
        low = text.lower()
        tokens = [contact.name, *contact.aliases, *relationship_phrases(contact.relationship)]
        idx, token = -1, ""
        for candidate in tokens:
            if not candidate:
                continue
            found = low.find(candidate.lower())
            if found != -1:
                idx, token = found, candidate
                break
        if idx == -1:
            return None
        rest = text[idx + len(token):].strip(" ,.")
        for prefix in ("that ", "to ", "saying ", "says ", "said ", "hey ", "hi "):
            if rest.lower().startswith(prefix):
                rest = rest[len(prefix):].strip()
                break
        return rest.strip().strip('"').strip("'") or None

    def _extract_contact(self, text: str) -> Optional[str]:
        """Extract a contact name from 'tell disha ...' style patterns."""
        stop_words = {
            "that", "to", "says", "said", "saying", "hey", "hi",
            "i", "i'll", "i'm", "im", "you", "we", "he", "she", "they", "it",
            "is", "am", "are", "was", "were", "will", "would", "can", "could",
            "should", "please", "and", "also", "just", "about", "when", "where",
            "what", "how", "for", "from", "with", "a", "an", "the",
        }
        for prefix in ("tell ", "message ", "reply to "):
            if prefix in text:
                after = text.split(prefix, 1)[1]
                words = after.split()
                if words:
                    # "my wife" / "the spouse" are valid relationship targets,
                    # even though "my"/"the" are otherwise stop words.
                    first = words[0].strip(",.!?").lower()
                    if first in ("my", "our", "the", "their", "his", "her") and len(words) > 1:
                        second = words[1].strip(",.!?")
                        if second.lower() not in stop_words and "'" not in second:
                            return f"{words[0].capitalize()} {second.capitalize()}"
                    # Take leading name words, stopping at the first verb/pronoun
                    # so "tell Disha I'll be late" resolves to "Disha", not
                    # "Disha I'll Be".
                    name_parts = []
                    for w in words[:3]:
                        clean = w.strip(",.!?")
                        if clean in stop_words or "'" in clean:
                            break
                        name_parts.append(w.capitalize())
                    return " ".join(name_parts) if name_parts else None
        return None

    def _extract_message_after(self, text: str) -> Optional[str]:
        """Extract message content after contact name."""
        for separator in (" that ", " to ", " says ", " said ", " hey ", " hi "):
            if separator in text:
                return text.split(separator, 1)[1].strip().strip('"').strip("'")
        return None

    def _extract_reply_intent(self, text: str) -> Optional[dict]:
        """Parse "reply to <who>'s email saying <body>" without guessing.

        A bare "reply to Disha" returns None so it keeps its existing messaging
        behavior; only explicit email/mail wording becomes an email reply.
        """
        import re

        lower = text.lower().strip()
        if not re.search(r"\b(?:reply|respond)\b", lower):
            return None
        if not re.search(r"\b(?:email|mail)\b", lower):
            return None

        body_match = re.search(
            r"\b(?:saying|that|with(?: the)? message)\s+(.+)$", text, re.I
        )
        if not body_match:
            return None
        body = body_match.group(1).strip().strip('"').strip("'")
        if not body:
            return None

        who_match = re.search(
            r"\b(?:reply|respond)\s+to\s+(.+?)(?:'s)?\s+(?:email|mail)\b", text, re.I
        )
        who = ""
        if who_match:
            who = who_match.group(1).strip()
            who = re.sub(r"^(?:the|this|that)\b\s*", "", who, flags=re.I).strip()
            if who.lower() in ("", "the", "this", "that", "email", "mail"):
                who = ""
        return {"who": who, "body": body}

    def _find_email_target(self, session: CallSession, who: str) -> Optional[DigestItem]:
        """Find the digest email the reply intent points at.

        An empty ``who`` means "this email" — the first email in the digest.
        A name only matches a sender it actually appears in; no fuzzy guessing.
        """
        emails = self._items_of_type(session, "email")
        if not emails:
            return None
        if not who:
            return emails[0]
        needle = who.lower()
        for item in emails:
            if needle in (item.sender or "").lower():
                return item
            if needle in (item.sender_email or "").lower():
                return item
        return None

    def _extract_reaction(self, text: str) -> Optional[str]:
        """Extract emoji reaction from text."""
        emoji_map = {
            "thumbs up": "👍",
            "like": "👍",
            "love": "❤️",
            "heart": "❤️",
            "laugh": "😄",
            "wow": "😮",
            "sad": "😢",
            "pray": "🙏",
        }
        for keyword, emoji in emoji_map.items():
            if keyword in text:
                return emoji
        # Check for direct emoji
        for emoji in ("👍", "❤️", "😄", "😮", "😢", "🙏"):
            if emoji in text:
                return emoji
    def _extract_mail_intent(self, text: str) -> Optional[dict]:
        """Extract mail intent, recipient, and message from utterance."""
        lower = text.lower().strip()
        # Strip common app-open prefixes
        for prefix in (
            "open mail,", "open mail and", "open mail",
            "open gmail,", "open gmail and", "open gmail",
            "open inbox,", "open inbox and", "open inbox",
        ):
            if lower.startswith(prefix):
                lower = lower[len(prefix):].strip(", ").strip()
                break

        import re
        # Pattern 1: send (a) mail/email to <recipient> saying/that/with message/: <body>
        pattern_with_body = (
            r"\b(?:send\s+(?:a\s+)?(?:mail|email)\s+to|mail\s+to|email\s+to|mail|email)\s+"
            r"([a-zA-Z0-9_\s]+?)\s+"
            r"(?:saying|that|with message|with body|telling (?:him|them|her)|:)\s+"
            r"(.+)"
        )
        m = re.search(pattern_with_body, lower, re.IGNORECASE)
        if m:
            recipient = m.group(1).strip()
            # Extract raw body matching the position from original text to preserve casing
            for sep in (" saying ", " that ", " with message ", " with body ", " : ", ": "):
                idx = text.lower().find(sep)
                if idx != -1:
                    raw_body = text[idx + len(sep):].strip().strip('"').strip("'")
                    return {"recipient": recipient, "body": raw_body}
            return {"recipient": recipient, "body": m.group(2).strip().strip('"').strip("'")}

        # Pattern 2: send (a) mail/email to <recipient> (no body)
        pattern_no_body = (
            r"\b(?:send\s+(?:a\s+)?(?:mail|email)\s+to|mail\s+to|email\s+to|mail|email)\s+"
            r"([a-zA-Z0-9_\s]+)$"
        )
        m2 = re.search(pattern_no_body, lower, re.IGNORECASE)
        if m2:
            recipient = m2.group(1).strip()
            return {"recipient": recipient, "body": None}

        return None

    # ── session access ────────────────────────────────────────────────────

    def get_session(self, call_id: str) -> Optional[CallSession]:
        return self._sessions.get(call_id)

    def get_active_sessions(self) -> list[CallSession]:
        return [
            s
            for s in self._sessions.values()
            if s.status not in (DigestStatus.COMPLETED, DigestStatus.FAILED)
        ]

    def get_all_sessions(self) -> list[CallSession]:
        return list(self._sessions.values())
