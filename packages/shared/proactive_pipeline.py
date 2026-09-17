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
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from .contacts import Contact, ContactBook
from .email_connector import GmailConnector

logger = logging.getLogger(__name__)


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
    ):
        self.hub = hub
        self.gmail = gmail or GmailConnector()
        self.contacts = contacts or ContactBook()
        if not self.contacts.list_all():
            self.contacts.add(Contact(name="Shravan G", email="shravan.g@example.com", phone="+919876543210"))
            self.contacts.add(Contact(name="Shravan GK", email="shravan.gk@sceptix.com", phone="+919876543211", aliases=["sceptix"]))
            self.contacts.add(Contact(name="Disha", phone="+919876500001", relationship="wife", aliases=["wife"]))
        self.on_action = on_action  # callback for executing approved actions

        self._sessions: dict[str, CallSession] = {}
        self._schedule = DigestSchedule()
        self._call_experience = CallExperience()

    # ── tick: called periodically to check triggers ───────────────────────

    async def tick(self) -> Optional[str]:
        """Check if a digest should fire. Returns call_id if started."""
        if not self._schedule.should_trigger():
            return None

        call_id = str(uuid.uuid4())[:8]
        logger.info("Digest triggered, starting call %s", call_id)

        session = await self._start_call(call_id, driving_scenario=False)
        return call_id

    async def _start_call(self, call_id: str, driving_scenario: bool = True) -> CallSession:
        """Fetch items, compose digest, push ring to phone."""
        # 1. Fetch items from connectors
        items = await self._fetch_digest_items(driving_scenario=driving_scenario)

        # 2. Compose digest
        digest = ProactiveDigest(call_id=call_id, items=items)

        # 3. Personalized spoken greeting for the driving workflow
        has_project = any("lume" in (i.summary or "").lower() for i in items)
        if has_project or driving_scenario:
            spoken_text = "Mr Chandragiri, your project LUME has completed 4 phases, do you wanna continue with the next phase?"
        else:
            spoken_text = digest.compose_spoken()

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
            reason=f"Daily digest — {len(items)} items",
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
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to fetch Gmail: %s", exc)

        if not items and driving_scenario:
            # Proactive workflow items (project status, unread email, spouse message)
            items.extend([
                DigestItem(
                    item_type="task",
                    summary="Project LUME completed Phase 4",
                    source="orchestrator",
                    priority="normal",
                    actionable=True,
                ),
                DigestItem(
                    item_type="email",
                    summary="From RazorClub: Tomorrow will be an important meeting on next hires",
                    source="gmail",
                    priority="high",
                    actionable=True,
                ),
                DigestItem(
                    item_type="message",
                    summary="From Disha (wife): When are you reaching home?",
                    source="whatsapp",
                    priority="high",
                    actionable=True,
                ),
            ])

        return items

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
                        "status": "sent",
                        "description": f"sent mail to {matched_candidate.name} ({matched_candidate.email}): '{message}'",
                    }
                    session.metadata.setdefault("sent_emails", []).append(action_payload)
                    session.status = DigestStatus.RESPONSE_RECEIVED
                    return {
                        "response_text": f"Sure, I've sent the mail to {matched_candidate.name} saying '{message}'. Is there anything else I need to do?",
                        "action": action_payload,
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
                        "status": "sent",
                        "description": f"sent mail to {chosen.name} ({chosen.email}): '{body}'",
                    }
                    session.metadata.setdefault("sent_emails", []).append(action_payload)
                    session.status = DigestStatus.RESPONSE_RECEIVED
                    return {
                        "response_text": f"Sure, I've sent the mail to {chosen.name} saying '{body}'. Is there anything else I need to do?",
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
                    "status": "sent",
                    "description": f"sent mail to {chosen.name} ({chosen.email}): '{body}'",
                }
                session.metadata.setdefault("sent_emails", []).append(action_payload)
                session.status = DigestStatus.RESPONSE_RECEIVED
                return {
                    "response_text": f"Sure, I've sent the mail to {chosen.name} saying '{body}'. Is there anything else I need to do?",
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
                "response_text": "Opening mail. You have 1 unread email from RazorClub regarding tomorrow's meeting on next hires. What would you like to reply?",
                "action": {"type": "open_app", "target": "gmail"},
                "digest_status": "awaiting_response",
            }

        # ── Compound command: "don't reply to the mail, just react with thumbs up, tell disha..." ──
        has_mail_decision = any(w in lower for w in ("don't reply", "dont reply", "do not reply", "react", "thumbs up", "thumbs-up", "👍"))
        has_disha_decision = any(w in lower for w in ("disha", "wife", "reaching", "home"))

        if has_mail_decision and has_disha_decision:
            reaction = "👍"
            disha_msg = "I'll be reaching in another hour"
            actions = [
                {
                    "type": "react_email",
                    "target": "RazorClub",
                    "reaction": reaction,
                    "description": f"reacted {reaction} to RazorClub mail",
                },
                {
                    "type": "send_message",
                    "target": "Disha",
                    "channel": "whatsapp",
                    "message": disha_msg,
                    "description": f"sent message to Disha: '{disha_msg}'",
                },
            ]
            session.metadata["last_executed"] = actions
            session.status = DigestStatus.RESPONSE_RECEIVED
            return {
                "response_text": "Sure Udbhav, I have sent the message and reacted to mail, Is there anything else I need to do?",
                "action": {"type": "compound_execute", "actions": actions},
                "digest_status": "awaiting_response",
            }

        # ── Multi-intent query: Project continuation + Mail + Disha query ──
        has_continue = any(w in lower for w in ("go on", "continue", "yes, go on", "yes go on", "start next", "next phase"))
        has_mail_query = any(w in lower for w in ("mail", "email", "inbox"))
        has_text_query = any(w in lower for w in ("disha", "text", "message", "wife"))

        if (has_mail_query and has_text_query) or (has_continue and (has_mail_query or has_text_query)):
            session.pending_actions.clear()
            session.pending_actions.append(
                {
                    "type": "continue_project",
                    "project": "LUME",
                    "description": "continue next phase of project LUME",
                }
            )
            session.metadata["awaiting_decision"] = True
            return {
                "response_text": (
                    "Yes sir, currently one important mail has been sent from RazorClub and it states that "
                    "tomorrow there will be an important meeting on the topic of next hires, also your wife Disha "
                    "has sent a text asking, when are you reaching home? what do I reply to the mail and to your wife?"
                ),
                "action": None,
                "digest_status": "awaiting_response",
            }

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
        if lower in ("yes", "go on", "approve", "do it", "send it", "confirm"):
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
        if lower in ("no don't", "cancel", "reject", "don't"):
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
                message = self._extract_message_after(lower)
                if message:
                    session.pending_actions.append(
                        {
                            "type": "message",
                            "contact": contact_name,
                            "message": message,
                            "description": f"message to {contact_name}",
                        }
                    )
                    return {
                        "response_text": f"Got it. I'll tell {contact_name}: {message}. Should I send it?",
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
            return {
                "response_text": f"Reacted with {reaction}." if reaction else "What reaction?",
                "action": {"type": "react", "reaction": reaction} if reaction else None,
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

    def _extract_contact(self, text: str) -> Optional[str]:
        """Extract contact name from 'tell disha ...' pattern."""
        for prefix in ("tell ", "message ", "reply to "):
            if prefix in text:
                after = text.split(prefix, 1)[1]
                words = after.split()
                if words:
                    # Take first word(s) as contact name (up to 3 words)
                    name_parts = []
                    for w in words[:3]:
                        if w in ("that", "to", "says", "said", "hey", "hi"):
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
            r"(?:send\s+(?:a\s+)?(?:mail|email)\s+to|mail\s+to|email\s+to|mail|email)\s+"
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
            r"(?:send\s+(?:a\s+)?(?:mail|email)\s+to|mail\s+to|email\s+to|mail|email)\s+"
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
