"""Desktop Agent — main loop tying wake word → STT → dialogue → execute → TTS."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from packages.shared.config import ViorisConfig
from packages.shared.dialogue import DialogueOrchestrator, OrchestratorResult
from packages.shared.contacts import ContactBook, ContactResolver
from packages.shared.learning import PreferenceStore, PatternLearner, AdaptiveResponder
from packages.shared.smart_home import HomeAssistantConnector
from packages.shared.email_connector import GmailConnector
from packages.shared.browser import BrowserManager
from packages.shared.multi_agent import AgentOrchestrator, AgentRole, AgentTask
from packages.shared.call_experience import CallExperience, CallState, RingEvent, RingSource
from packages.shared.permission_engine import PermissionEngine

logger = logging.getLogger("vioris.agent")


class AgentState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    ERROR = "error"


@dataclass
class AgentEvent:
    event_type: str
    data: Any = None
    timestamp: float = field(default_factory=time.time)


class DesktopAgent:
    """The main Vioris agent — connects all subsystems."""

    def __init__(self, config: Optional[ViorisConfig] = None):
        self.config = config or ViorisConfig.from_env()
        self.state = AgentState.IDLE

        # Core subsystems
        self.permission_engine = PermissionEngine()
        self.dialogue = DialogueOrchestrator()
        self.contacts = ContactBook()
        self.contact_resolver = ContactResolver(self.contacts)
        self.call_experience = CallExperience()

        # Learning
        self.preferences = PreferenceStore()
        self.patterns = PatternLearner()
        self.responder = AdaptiveResponder(self.preferences, self.patterns)

        # Connectors
        self.ha = HomeAssistantConnector(base_url=self.config.ha_url, token=self.config.ha_token)
        self.gmail = GmailConnector()
        self.browser = BrowserManager()

        # Multi-agent
        self.orchestrator = AgentOrchestrator()
        self._register_agents()

        # Event handlers
        self._event_handlers: dict[str, list[Callable]] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()

        # Metrics
        self._start_time = time.time()
        self._commands_processed = 0
        self._errors = 0

    def _register_agents(self):
        self.orchestrator.register_agent(AgentRole.EMAIL)
        self.orchestrator.register_agent(AgentRole.BROWSER)
        self.orchestrator.register_agent(AgentRole.SMART_HOME)
        self.orchestrator.register_agent(AgentRole.COMPUTER)
        self.orchestrator.register_agent(AgentRole.CALENDAR)
        self.orchestrator.register_agent(AgentRole.FILE)

    def on(self, event_type: str, handler: Callable):
        self._event_handlers.setdefault(event_type, []).append(handler)

    async def _emit(self, event: AgentEvent):
        for handler in self._event_handlers.get(event.event_type, []):
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        await self._event_queue.put(event)

    async def start(self):
        logger.info("Starting Vioris Desktop Agent...")
        self.config.ensure_dirs()

        if self.config.ha_token:
            self.ha.connect()
            logger.info("Home Assistant connected")

        if self.config.wake_word_enabled:
            logger.info(f"Wake word listening: '{self.config.wake_word_phrase}'")

        await self._emit(AgentEvent("agent_started"))
        logger.info("Vioris Agent ready")

    async def stop(self):
        logger.info("Stopping Vioris Desktop Agent...")
        await self.browser.close_all()
        await self._emit(AgentEvent("agent_stopped"))

    async def process_utterance(self, utterance: str, call_id: Optional[str] = None) -> dict:
        self.state = AgentState.PROCESSING
        self._commands_processed += 1

        try:
            # 1. Decompose and understand
            result = self.dialogue.process(utterance=utterance)

            # 2. Resolve contacts if mentioned
            targets = []
            for si in result.sub_intents:
                if si.targets:
                    targets.extend(si.targets)
            resolved_contacts = self.contact_resolver.resolve_all(targets) if targets else []

            # 3. Route to appropriate agents
            agent_tasks = self.orchestrator.decompose_request(utterance)

            # 4. Execute tasks
            agent_results = []
            if agent_tasks:
                agent_results = await self.orchestrator.execute_parallel(agent_tasks)

            # 5. Compose response
            response = result.composed_response or self.responder.adapt_response("Done.")

            # 6. Record pattern
            self.patterns.record(utterance, response)

            self.state = AgentState.IDLE

            return {
                "utterance": utterance,
                "response": response,
                "sub_intents": [{"text": si.text, "type": si.intent_type} for si in result.sub_intents],
                "actionables": [{"item": a.item, "action": a.suggested_action} for a in result.actionables],
                "contacts_resolved": resolved_contacts,
                "agent_results": [r.to_dict() for r in agent_results],
                "trust_level": result.trust_level.value,
                "requires_approval": result.requires_approval,
            }

        except Exception as e:
            self._errors += 1
            self.state = AgentState.ERROR
            logger.error(f"Error processing: {e}")
            return {"error": str(e), "utterance": utterance}

    async def handle_call(self, caller_name: str, source: str = "incoming") -> dict:
        call_id = f"call_{int(time.time())}"
        event = RingEvent(
            call_id=call_id,
            source=RingSource(source),
            caller_name=caller_name,
        )
        self.call_experience.start_ring(event)
        self.state = AgentState.LISTENING

        await self._emit(AgentEvent("call_started", {"call_id": call_id, "caller": caller_name}))

        return {
            "call_id": call_id,
            "status": "ringing",
            "caller": caller_name,
            "message": f"Incoming call from {caller_name}. Say 'accept' to answer.",
        }

    async def end_call(self) -> dict:
        self.call_experience.end_call()
        self.state = AgentState.IDLE
        await self._emit(AgentEvent("call_ended"))
        return {"status": "ended"}

    async def get_digest(self, items: Optional[list] = None) -> dict:
        from packages.shared.call_experience import ProactiveDigest, DigestItem
        digest_items = [DigestItem(**i) for i in (items or [])]
        digest = self.call_experience.get_digest("digest", digest_items)
        return {"spoken": digest.compose_spoken(), "items": [i.to_dict() for i in digest_items]}

    def get_status(self) -> dict:
        return {
            "state": self.state.value,
            "uptime_seconds": int(time.time() - self._start_time),
            "commands_processed": self._commands_processed,
            "errors": self._errors,
            "connected_devices": 0,
            "ha_connected": self.ha.is_connected(),
            "gmail_connected": self.gmail.is_connected(),
            "browser_sessions": len(self.browser.list_sessions()),
        }

    def get_preferences(self) -> dict:
        return self.preferences.export()

    def set_preference(self, key: str, value: Any):
        self.preferences.set(key, value)

    def get_contacts(self) -> list[dict]:
        return [c.to_dict() for c in self.contacts.list_all()]

    def add_contact(self, name: str, phone: str = "", email: str = "", **kwargs):
        from packages.shared.contacts import Contact
        self.contacts.add(Contact(name=name, phone=phone, email=email, **kwargs))
