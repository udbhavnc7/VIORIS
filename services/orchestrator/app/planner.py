"""
Planner (Phase 2, Prompt 2.2).

Takes a transcript and returns an ordered list of TaskSteps as a plan object —
nothing executes here. The LLM proposes tools + arguments + order; the risk
level of every step is pulled from the STATIC permission registry, never from
the model. Unregistered tools proposed by the LLM are flagged as blocked, not
silently allowed.

The plan is returned as JSON before anything runs, so a caller (orchestrator /
human) can review it. See docs/02-architecture.md §3 (Critic Agent reviews
plans before execution).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from packages.shared.intent_classifier import classify_intent, IntentType
from packages.shared.permission_engine import PermissionEngine, UnknownToolError
from packages.shared.schemas import TaskStep

from .llm_client import default_llm_backend, PlannerBackendError
from .tool_schema import build_tool_schema

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Vioris's planner. Break the user request into an ordered list of "
    "steps using ONLY the provided tools. Reply with tool_calls only — one call "
    "per step, in execution order. Each tool_call must be exactly one of the "
    "offered tools; do not invent tools. If the request cannot be satisfied by "
    "the offered tools, make no tool calls. Never include risk levels or "
    "permission labels in your output."
)


@dataclass
class Plan:
    request: str
    steps: list[TaskStep] = field(default_factory=list)
    blocked_tools: list[dict] = field(default_factory=list)  # proposed but unregistered
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "request": self.request,
            "steps": [_step_dict(s) for s in self.steps],
            "blocked_tools": self.blocked_tools,
            "error": self.error,
        }


class Planner:
    def __init__(self, backend: callable | None = None, deterministic: bool | None = None) -> None:
        self.backend = backend or default_llm_backend()
        # Deterministic workflow routing is the offline-first default. An
        # explicitly injected backend means the caller wants the model to plan
        # (tests, custom models), so routing only runs when asked for it.
        self.deterministic = deterministic if deterministic is not None else backend is None

    def plan(self, transcript: str) -> Plan:
        """Plan a transcript without executing anything. Returns JSON-able Plan.

        Routes simple intents (single queries/commands) through a fast direct
        path that skips the LLM planner entirely. Deterministic multi-step
        workflows (briefing, messaging, reservations) also route here so the
        product works without a local model installed. Complex or ambiguous
        requests go through the full LLM planning pipeline.
        """
        if self.deterministic:
            direct = self._workflow_route(transcript)
            if direct is not None:
                return direct

        # Fast path: classify intent before hitting the LLM
        intent = classify_intent(transcript)
        if intent.is_simple:
            direct = self._direct_route(transcript, intent)
            if direct.steps:
                logger.info(
                    "intent classified as %s (confidence=%.2f, pattern=%s) — using direct route",
                    intent.intent_type.value,
                    intent.confidence,
                    intent.matched_pattern,
                )
                return direct
            # Direct route didn't produce steps — fall through to LLM

        # Full path: LLM planner for complex/unknown intents
        tools = build_tool_schema()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ]
        try:
            result = self.backend.chat_with_tools(messages, tools)
        except PlannerBackendError as exc:
            logger.warning("planner backend failed: %s", exc)
            return Plan(request=transcript, error=str(exc))
        except UnknownToolError as exc:
            return Plan(request=transcript, error=str(exc))

        return self._to_plan(transcript, result.get("tool_calls", []))

    def _workflow_route(self, transcript: str) -> Plan | None:
        """Deterministic routes for the product's primary workflows.

        These run without an LLM, which keeps the core loop usable offline and
        makes demo/observe workflows reproducible. The LLM never assigns risk:
        each tier still comes from the frozen registry. Returns None when no
        workflow matches so the normal classifier/LLM path can take over.
        """
        text = transcript.strip()
        lower = text.lower()
        # A compound request ("check my email and open chrome") must never be
        # narrowed to its first clause here — that would silently drop the rest.
        compound = bool(re.search(r"\b(and|then|also|plus)\b", lower))

        def make(
            tool: str,
            args: dict | None = None,
            agent: str = "system",
            reason: str = "",
        ) -> TaskStep:
            return TaskStep(
                agent=agent,
                tool=tool,
                risk_level=PermissionEngine.classify(tool).tier,
                result={"args": args or {}},
                reasoning=reason,
            )

        # ── briefing: three observe reads, nothing sent ───────────────────
        if re.search(r"\b(brief|briefing|digest|catch me up|on my plate|daily summary)\b", lower):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "gmail.read_unread",
                        {"hours": 24, "max_results": 5},
                        reason="A briefing starts with unread mail so the summary can surface anything waiting on you.",
                    ),
                    make(
                        "calendar.read_upcoming",
                        {"hours": 24, "max_results": 5},
                        reason="A briefing also needs today's calendar so the spoken summary can name what is next.",
                    ),
                    make(
                        "whatsapp.read_digest",
                        {"hours": 24, "max_messages": 5},
                        reason="Messages are the third read-only source in the briefing workflow; nothing is sent.",
                    ),
                ],
            )

        # ── messaging: execute-tier, pauses for approval ─────────────────
        msg = re.search(
            r"\b(?:send|text|message)\s+(?:a\s+)?(?:message\s+|text\s+)?to\s+(.+?)\s+"
            r"(?:saying|that says|about|:)\s*(.+)$",
            text,
            re.I,
        )
        if msg:
            recipient = msg.group(1).strip(" ,.")
            content = msg.group(2).strip().strip('"').strip("'")
            if recipient and content:
                return Plan(
                    request=transcript,
                    steps=[
                        make(
                            "system.send_message",
                            {
                                "recipient": recipient,
                                "content": content,
                                "channel": "whatsapp",
                            },
                            reason=(
                                "The request named a recipient and message body, so this is the "
                                "messaging tool. It is Execute-tier and pauses for approval before "
                                "anything leaves the machine."
                            ),
                        )
                    ],
                )

        # ── reservations: observe search first ───────────────────────────
        reserve = re.search(
            r"\b(?:reserve|book|reservation)\b.*?\bat\s+(.+?)(?:\s+for\s+(\d+))?(?:\s+(?:on|tomorrow|today).*)?$",
            text,
            re.I,
        )
        if re.search(r"\b(reserve|book a table|reservation)\b", lower) and reserve:
            venue = reserve.group(1).strip(" ,.")
            party = int(reserve.group(2)) if reserve.group(2) else 2
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "reservations.search_slots",
                        {"venue": venue, "party_size": party},
                        reason=(
                            "Searching available slots is Observe-tier, so the plan looks before "
                            "it books. Creating a reservation would need a separate approval."
                        ),
                    )
                ],
            )

        # ── single observe reads ─────────────────────────────────────────
        if compound:
            return None
        if re.search(r"\b(unread|inbox|mail|email)\b", lower) and re.search(
            r"\b(check|read|any|what|show|new)\b", lower
        ):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "gmail.read_unread",
                        reason="The request asked to check mail, and this is the read-only unread-mail tool.",
                    )
                ],
            )
        if re.search(r"\b(calendar|schedule|meetings?|appointments?)\b", lower):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "calendar.read_upcoming",
                        reason="The request mentioned the calendar, so the plan reads upcoming events instead of changing them.",
                    )
                ],
            )
        if re.search(r"\b(whatsapp|messages?|texts?)\b", lower) and re.search(
            r"\b(check|read|any|what|show|new)\b", lower
        ):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "whatsapp.read_digest",
                        reason="The request asked for messages, and this tool reads a digest without sending anything.",
                    )
                ],
            )
        contact = re.search(r"\b(?:find|search|look up)\s+(?:contact\s+)?(.+?)(?:\s+in\s+contacts)?$", text, re.I)
        if "contact" in lower and contact:
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "contacts.search",
                        {"query": contact.group(1).strip(" .?")},
                        reason="The request was a contact lookup, so the plan searches the contact book read-only.",
                    )
                ],
            )

        # ── smart home: read status or execute-gated control ─────────────
        if re.search(r"\b(smart\s*home|devices?|lights?|thermostat|sensors?)\b", lower) and re.search(
            r"\b(check|status|list|show|what|read)\b", lower
        ):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "smart_home.device_status",
                        reason="The request asked for smart-home status, which is Observe-tier and changes nothing.",
                    )
                ],
            )
        toggle = re.search(
            r"\bturn\s+(on|off)\s+(?:the\s+)?(.+)$",
            text,
            re.I,
        )
        if toggle:
            action = toggle.group(1).lower()
            target_name = toggle.group(2).strip(" .?")
            device_id = (
                "light.living_room"
                if "living" in target_name.lower() or "light" in target_name.lower()
                else re.sub(r"[^a-z0-9]+", "_", target_name.lower()).strip("_") or "device"
            )
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "smart_home.control",
                        {
                            "device_id": device_id,
                            "device_name": target_name,
                            "category": "light" if "light" in target_name.lower() else "device",
                            "action": action,
                            "value": action,
                        },
                        reason="Controlling a smart-home device is Execute-tier and pauses at the approval gate with the exact target.",
                    )
                ],
            )

        # ── notes / cloud files / bookmarks / browser history ────────────
        if re.search(r"\bnotes?\b", lower) and re.search(r"\b(check|read|list|recent|show|my)\b", lower):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "notes.list_recent",
                        reason="The request asked for recent notes; this is an Observe-tier read.",
                    )
                ],
            )
        if re.search(r"\b(cloud files|drive|recent files|documents)\b", lower) and re.search(
            r"\b(check|list|recent|show|my)\b", lower
        ):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "cloud_files.list_recent",
                        reason="The request asked for recent cloud files; metadata-only Observe read.",
                    )
                ],
            )
        bm = re.search(r"\b(?:search|find|check)\s+bookmarks?(?:\s+for)?\s*(.*)$", text, re.I)
        if "bookmark" in lower:
            query = bm.group(1).strip(" .?") if bm else ""
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "bookmarks.search",
                        {"query": query},
                        reason="Searching saved browser bookmarks is Observe-tier and read-only.",
                    )
                ],
            )
        if re.search(r"\b(history|recent pages|visited)\b", lower):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "history.recent",
                        reason="Reading recent browser history is Observe-tier and read-only.",
                    )
                ],
            )

        # ── telephony: prepare a call (never auto-dials) ─────────────────
        prep_call = re.search(
            r"\b(?:prepare|draft)\s+(?:a\s+)?call\s+(?:to\s+)?(.+?)(?:\s+(?:about|for)\s+(.+))?$",
            text,
            re.I,
        )
        if prep_call:
            recipient = prep_call.group(1).strip(" ,.")
            purpose = (prep_call.group(2) or "").strip(" ,.")
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "telephony.prepare_call",
                        {"recipient": recipient, "purpose": purpose},
                        reason="Preparing a call is Prepare-tier: it drafts a talking-point brief and never dials autonomously.",
                    )
                ],
            )

        # ── computer: windows / screenshot / OCR ─────────────────────────
        if re.search(r"\b(open windows|list windows|what windows)\b", lower):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "computer.list_windows",
                        agent="computer",
                        reason="Listing open window titles is Observe-tier.",
                    )
                ],
            )
        if re.search(r"\b(screenshot|capture screen|capture the screen)\b", lower):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "computer.capture_screenshot",
                        agent="computer",
                        reason="Capturing one screenshot is Observe-tier.",
                    )
                ],
            )
        if re.search(r"\b(read screen|read the screen|what(?:'s| is) on (?:my |the )?screen)\b", lower):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "computer.read_screen_text",
                        agent="computer",
                        reason="Extracting visible text via OCR is Observe-tier.",
                    )
                ],
            )

        # ── isolated browser: navigate / read page ───────────────────────
        nav = re.search(r"\b(?:navigate|browse|open|go)\s+(?:to\s+)?(https?://\S+)", text, re.I)
        if nav:
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "browser.navigate",
                        {"url": nav.group(1).rstrip(".,")},
                        agent="browser",
                        reason="Opening a URL in the isolated automation browser is Observe-tier and stops on login/CAPTCHA walls.",
                    )
                ],
            )
        if re.search(r"\b(read (?:this |the )?(?:page|webpage))\b", lower):
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "browser.read_page",
                        agent="browser",
                        reason="Reading the active automation browser page is Observe-tier.",
                    )
                ],
            )

        # ── allow-listed files: search ───────────────────────────────────
        fsearch = re.search(r"\b(?:search|find)\s+files?(?:\s+for)?\s+(.+)$", text, re.I)
        if fsearch:
            return Plan(
                request=transcript,
                steps=[
                    make(
                        "file.search_files",
                        {"query": fsearch.group(1).strip(" .?")},
                        agent="documents",
                        reason="Searching inside allow-listed directories is Observe-tier.",
                    )
                ],
            )

        return None

    def _direct_route(self, transcript: str, intent: "ClassifiedIntent") -> Plan:
        """Handle simple intents without the LLM planner.

        Maps known simple patterns to their tool calls. Unrecognized simple
        intents still fall through to the LLM (returns empty steps so caller
        can decide to escalate).
        """
        from packages.shared.intent_classifier import ClassifiedIntent

        text = transcript.strip().lower()

        # Time queries — direct, no LLM needed
        if intent.matched_pattern == "time_query":
            return Plan(
                request=transcript,
                steps=[
                    TaskStep(
                        agent="system",
                        tool="system.get_time",
                        risk_level=PermissionEngine.classify("system.get_time").tier,
                        result={"args": {}},
                        reasoning="The intent classifier matched a time query; this is the read-only clock tool.",
                    )
                ],
            )

        # Stop commands
        if intent.matched_pattern == "stop_command":
            return Plan(
                request=transcript,
                steps=[
                    TaskStep(
                        agent="system",
                        tool="system.stop",
                        risk_level=PermissionEngine.classify("system.stop").tier,
                        result={"args": {}},
                        reasoning="The request was an explicit stop command; the kill switch routes to the manager and halts every task.",
                    )
                ],
            )

        # Open commands — extract app name heuristically
        if intent.matched_pattern in ("open_command", "launch_command"):
            # Try to extract app name after "open"/"launch"
            import re
            m = re.search(r"\b(?:open|launch)\s+(.+)", text, re.I)
            app_name = m.group(1).strip() if m else ""
            if app_name:
                return Plan(
                    request=transcript,
                    steps=[
                        TaskStep(
                            agent="system",
                            tool="system.open_app",
                            risk_level=PermissionEngine.classify("system.open_app").tier,
                            result={"args": {"app": app_name}},
                            reasoning=f"The request asked to open '{app_name}', and this is the observe-tier app launcher.",
                        )
                    ],
                )

        # Reminder commands — extract reminder text and time
        if intent.matched_pattern in ("reminder_command",):
            import re
            m = re.search(r"(?:set (?:a )?reminder|remind me)\s+(?:to\s+)?(.+?)(?:\s+(?:at|for|on)\s+(.+))?$", text, re.I)
            if m:
                reminder_text = (m.group(1) or "").strip()
                at = (m.group(2) or "").strip()
                if reminder_text:
                    args = {"reminder": reminder_text}
                    if at:
                        args["at"] = at
                    return Plan(
                        request=transcript,
                        steps=[
                            TaskStep(
                                agent="system",
                                tool="system.set_reminder",
                                risk_level=PermissionEngine.classify("system.set_reminder").tier,
                                result={"args": args},
                                reasoning=(
                                    "The request was a reminder, so the plan stores it locally with "
                                    "the reminder tool; setting a reminder is Prepare-tier and needs "
                                    "no external send."
                                ),
                            )
                        ],
                    )

        # Unrecognized simple intent — return empty plan, caller escalates
        logger.info("simple intent '%s' not routable directly — falling through to LLM", intent.matched_pattern)
        return Plan(request=transcript)

    def _to_plan(self, transcript: str, tool_calls: list[dict]) -> Plan:
        plan = Plan(request=transcript)
        for i, call in enumerate(tool_calls):
            name = call.get("name")
            if not name:
                continue
            try:
                permission = PermissionEngine.classify(name)
            except UnknownToolError:
                plan.blocked_tools.append(
                    {"tool": name, "reason": "not in the frozen registry — refused"}
                )
                continue
            step = TaskStep(
                agent="system",
                tool=name,
                risk_level=permission.tier,  # static registry decides, never the LLM
                result={"args": call.get("arguments", {})},
                reasoning=(
                    f"The model proposed '{name}' for step {i + 1}; its risk tier is assigned "
                    "by the static permission registry, not by the model."
                ),
            )
            plan.steps.append(step)
        return plan


def _step_dict(step: TaskStep) -> dict:
    return {
        "step_id": step.step_id,
        "agent": step.agent,
        "tool": step.tool,
        "risk_level": step.risk_level.value,
        "arguments": step.result,
        "status": step.status.value,
        # Show why before execution, not only after the audit record exists.
        "reasoning": step.reasoning,
        "alternatives": step.alternatives,
    }
