"""
Vioris Permission Engine — Static Risk Registry.

A tool's risk tier is a property of the TOOL, registered at build time.
The LLM proposes plans; it never sets or reclassifies risk levels.
Unregistered tools are BLOCKED, not defaulted to observe.

See docs/03-permissions-and-security.md for the full permission matrix.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

import numpy as np

from packages.shared.schemas import PermissionResult, RiskTier, ToolRegistration

if TYPE_CHECKING:
    from packages.shared.voiceprint import VoiceprintManager

logger = logging.getLogger(__name__)


class UnknownToolError(Exception):
    """Raised when a tool is not in the static registry. Unregistered = blocked."""

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(
            f"Tool '{tool_name}' is not registered in the permission engine. "
            f"Unregistered tools are blocked by default. "
            f"Register it with a static risk tier before use."
        )


class PermissionEngine:
    """
    Deterministic, static permission engine.

    Rules:
    - Every tool must be registered with a fixed risk tier before it can be used.
    - The registry is populated at startup, not at runtime by the LLM.
    - Observe: no confirmation needed.
    - Prepare: no confirmation needed, but result is always shown.
    - Execute: explicit user confirmation required, diff card shown.
    - Critical: confirmation + second factor (biometric/PIN) + cooldown.
    - An unregistered tool raises UnknownToolError — it is never silently allowed.
    """

    # Class-level registry so it's truly static across the application
    _registry: ClassVar[dict[str, ToolRegistration]] = {}
    _frozen: ClassVar[bool] = False

    @classmethod
    def register(cls, registration: ToolRegistration) -> None:
        """Register a tool with its static risk classification.

        Must be called at startup. Once frozen, no new registrations are accepted.
        """
        if cls._frozen:
            raise RuntimeError(
                f"Cannot register tool '{registration.tool_name}' — "
                f"the permission registry is frozen. "
                f"Tools must be registered at startup, not at runtime."
            )

        if registration.tool_name in cls._registry:
            raise ValueError(
                f"Tool '{registration.tool_name}' is already registered. "
                f"Duplicate registration is not allowed."
            )

        # Enforce diff card fields for Execute/Critical tools
        if registration.tier in (RiskTier.EXECUTE, RiskTier.CRITICAL):
            if not registration.diff_card_fields:
                raise ValueError(
                    f"Tool '{registration.tool_name}' is tier '{registration.tier.value}' "
                    f"but has no diff_card_fields defined. "
                    f"Execute/Critical tools must declare what the approval card shows."
                )

        cls._registry[registration.tool_name] = registration
        logger.info(
            "Registered tool '%s' at tier '%s'",
            registration.tool_name,
            registration.tier.value,
        )

    @classmethod
    def freeze(cls) -> None:
        """Freeze the registry. No more tools can be registered after this.

        Call this after all tools are registered at startup.
        This prevents any runtime code (including LLM-driven code) from
        adding or modifying tool registrations.
        """
        cls._frozen = True
        logger.info("Permission registry frozen with %d tools registered.", len(cls._registry))

    # Voiceprint manager injected at startup for second-factor verification
    _voiceprint: ClassVar[VoiceprintManager | None] = None

    @classmethod
    def set_voiceprint(cls, vp: VoiceprintManager | None) -> None:
        """Inject the voiceprint manager for Critical-tier second-factor checks."""
        cls._voiceprint = vp

    @classmethod
    def classify(
        cls,
        tool_name: str,
        *,
        second_factor_verified: bool = False,
    ) -> PermissionResult:
        """Look up a tool's risk classification.

        Returns a PermissionResult with tier, confirmation requirements, etc.
        Raises UnknownToolError if the tool isn't registered — unregistered = blocked.

        For Critical-tier tools, `requires_second_factor` is True. The caller
        must then call `verify_critical_second_factor()` before execution.
        """
        if tool_name not in cls._registry:
            raise UnknownToolError(tool_name)

        reg = cls._registry[tool_name]

        return PermissionResult(
            tool_name=tool_name,
            tier=reg.tier,
            confirmation_required=reg.confirmation_required,
            requires_second_factor=reg.tier == RiskTier.CRITICAL,
            cooldown_seconds=5 if reg.tier == RiskTier.CRITICAL else 0,
            allowed=True,
            reason=f"Tool '{tool_name}' classified as '{reg.tier.value}'",
        )

    @classmethod
    def verify_critical_second_factor(
        cls,
        audio: np.ndarray | None = None,
        sample_rate: int = 16000,
    ) -> tuple[bool, str]:
        """Verify the second factor for a Critical-tier action.

        Uses voiceprint verification if a voiceprint manager is enrolled.
        Returns (passed: bool, reason: str).

        If no voiceprint is enrolled, verification FAILS CLOSED — the action
        is blocked. This enforces that Critical actions require enrollment first.
        """
        if cls._voiceprint is None:
            return False, "no voiceprint manager configured"

        if not cls._voiceprint.is_enrolled():
            return False, "voiceprint not enrolled — cannot verify Critical action"

        if audio is None or (hasattr(audio, "size") and audio.size == 0):
            return False, "no audio provided for voiceprint verification"

        passed, similarity = cls._voiceprint.verify(audio, sample_rate)
        if passed:
            return True, f"voiceprint verified (similarity={similarity:.4f})"
        return False, f"voiceprint rejected (similarity={similarity:.4f})"

    @classmethod
    def is_registered(cls, tool_name: str) -> bool:
        """Check if a tool is in the registry without raising."""
        return tool_name in cls._registry

    @classmethod
    def list_tools(cls) -> list[ToolRegistration]:
        """Return all registered tools. For admin/debug views."""
        return list(cls._registry.values())

    @classmethod
    def reset(cls) -> None:
        """Reset the registry. ONLY for use in tests."""
        cls._registry.clear()
        cls._frozen = False


# ─── Phase 1 Tool Registrations ──────────────────────────────────────────────
# Called at import time to populate the static registry.

_PHASE1_TOOLS = [
    ToolRegistration(
        tool_name="system.get_time",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report the current date and time",
    ),
    ToolRegistration(
        tool_name="system.open_app",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Open a desktop application by name",
    ),
    ToolRegistration(
        tool_name="system.set_reminder",
        tier=RiskTier.PREPARE,
        confirmation_required=False,
        description="Set a local reminder for a specified time",
    ),
    ToolRegistration(
        tool_name="system.stop",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Immediately halt all in-progress tasks",
    ),
]


def register_phase1_tools() -> None:
    """Register all Phase 1 tools in the permission engine."""
    for tool in _PHASE1_TOOLS:
        if not PermissionEngine.is_registered(tool.tool_name):
            PermissionEngine.register(tool)


# ─── Phase 2 demo tools (execute/critical tiers) ─────────────────────────────
# These exist so the approval flow (Prompt 2.3) has real execute/critical
# targets to gate. diff_card_fields is mandatory for these tiers — the approval
# card literally shows those fields.

_PHASE2_TOOLS = [
    ToolRegistration(
        tool_name="system.send_message",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Send a message to a contact",
        diff_card_fields=["recipient", "content", "channel"],
    ),
    ToolRegistration(
        tool_name="system.register_payment",
        tier=RiskTier.CRITICAL,
        confirmation_required=True,
        description="Register/authorize a payment",
        diff_card_fields=["payee", "currency", "amount", "account"],
    ),
]


def register_phase2_tools() -> None:
    """Register the Phase 2 demo tools (execute/critical) in the engine."""
    for tool in _PHASE2_TOOLS:
        if not PermissionEngine.is_registered(tool.tool_name):
            PermissionEngine.register(tool)


# ─── Phase 3 computer-agent tools (observe tier) ─────────────────────────────
# Reading the screen / listing windows changes nothing; the agent re-checks the
# screen after actions and reports success/failure itself (Prompt 3.1). Any
# tool that acts on the machine more broadly (typing, clicking, file ops) is
# later tiers and lives in the next prompts.

_PHASE3_COMPUTER_TOOLS = [
    ToolRegistration(
        tool_name="computer.list_windows",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="List currently open application windows with titles",
    ),
    ToolRegistration(
        tool_name="computer.capture_screenshot",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Capture a screenshot of the primary screen",
    ),
    ToolRegistration(
        tool_name="computer.read_screen_text",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Extract visible text from the screen via OCR/vision",
    ),
    ToolRegistration(
        tool_name="computer.open_app",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Open a desktop application by name",
    ),
]


def register_phase3_tools() -> None:
    """Register the Phase 3 computer-agent tools in the engine."""
    for tool in _PHASE3_COMPUTER_TOOLS:
        if not PermissionEngine.is_registered(tool.tool_name):
            PermissionEngine.register(tool)


# ─── Phase 5 remote-control tools (Prompt 5.3) ───────────────────────────────
# Screen mirroring + remote input from the phone. STARTING a remote session is
# Execute: it hands an external device control of the laptop, so it pauses for
# explicit approval and auto-expires. EACH remote input is also Execute-tier —
# gated inside the agent by the active, unexpired session, never by the LLM.

_REMOTE_TOOLS = [
    ToolRegistration(
        tool_name="computer.start_remote_session",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Open an explicit, auto-expiring remote-control session for a paired device",
        diff_card_fields=["device_id", "timeout_minutes"],
    ),
    ToolRegistration(
        tool_name="computer.remote_input",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Send a keyboard/mouse action to the laptop within an active remote session",
        diff_card_fields=["session_id", "action"],
    ),
    ToolRegistration(
        tool_name="computer.lock_workstation",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Remote-lock the laptop workstation immediately (phone 'lock now')",
        diff_card_fields=["device_id"],
    ),
]


def register_phase5_remote_tools() -> None:
    """Register the Phase 5 remote-control tools in the engine."""
    for tool in _REMOTE_TOOLS:
        if not PermissionEngine.is_registered(tool.tool_name):
            PermissionEngine.register(tool)


# ─── Phase 3 browser-agent tools ─────────────────────────────────────────────
# Reading/navigating is Observe. Filling a form field stages input locally
# (Prepare — shown, not sent). CLICKING can externally submit a form or trigger
# an account change, so it is Execute and requires the diff card to show the
# exact page + intended target before any click fires.

_PHASE3_BROWSER_TOOLS = [
    ToolRegistration(
        tool_name="browser.navigate",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Navigate the automation browser to a URL",
    ),
    ToolRegistration(
        tool_name="browser.read_page",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Read the current page's visible text",
    ),
    ToolRegistration(
        tool_name="browser.fill_field",
        tier=RiskTier.PREPARE,
        confirmation_required=False,
        description="Fill a form field on the current page",
    ),
    ToolRegistration(
        tool_name="browser.click_element",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Click an element on the current page",
        diff_card_fields=["url", "element"],  # approval must show what/where
    ),
]


def register_phase3_browser_tools() -> None:
    """Register the Phase 3 browser-agent tools in the engine."""
    for tool in _PHASE3_BROWSER_TOOLS:
        if not PermissionEngine.is_registered(tool.tool_name):
            PermissionEngine.register(tool)


# ─── Phase 3 file/shell tools (Prompt 3.3 — guardrails) ──────────────────────
# Reading/searching inside the allow-list is Observe. Moving/renaming an
# existing file changes the filesystem (Execute). DELETE is Critical: money,
# deletion, irreversible. shell.run_command executes a command on the laptop,
# so it is Execute with a diff card showing the exact command and args. The
# allow-list scope itself is enforced by the documents agent at call time —
# anything outside the configured roots or command allow-list is REFUSED, not
# attempted, regardless of tier.

_PHASE3_FILES_TOOLS = [
    ToolRegistration(
        tool_name="file.search_files",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Search for files matching a query inside the allow-listed directories",
    ),
    ToolRegistration(
        tool_name="file.read",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Read a text file inside the allow-listed directories",
    ),
    ToolRegistration(
        tool_name="file.move",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Move a file between two paths inside the allow-listed directories",
        diff_card_fields=["source", "destination"],
    ),
    ToolRegistration(
        tool_name="file.rename",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Rename a file inside the allow-listed directories",
        diff_card_fields=["path", "new_name"],
    ),
    ToolRegistration(
        tool_name="file.delete",
        tier=RiskTier.CRITICAL,
        confirmation_required=True,
        description="Permanently delete a file inside the allow-listed directories",
        diff_card_fields=["path"],  # irreversible, deletion is always Critical
    ),
    ToolRegistration(
        tool_name="terminal.run",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Run a whitelisted terminal command with arguments",
        diff_card_fields=["command", "args"],
    ),
]


def register_phase3_files_tools() -> None:
    """Register the Phase 3 file/shell tools in the engine."""
    for tool in _PHASE3_FILES_TOOLS:
        if not PermissionEngine.is_registered(tool.tool_name):
            PermissionEngine.register(tool)


# ─── Phase 6 connector tools (Prompt 6.1 — read-only Gmail) ──────────────────
# READING unread mail changes nothing externally, so observe tier (no
# confirmation). Sending/reply/delete are intentionally NOT registered by this
# connector — an unregistered tool is blocked even if some future code path
# tried to call it.

_PHASE6_CONNECTOR_TOOLS = [
    ToolRegistration(
        tool_name="gmail.read_unread",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Fetch a digest of unread email from the connected account",
    ),
    ToolRegistration(
        tool_name="gmail.digest_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report which Gmail account is connected and its read scope",
    ),
    ToolRegistration(
        tool_name="whatsapp.read_digest",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Fetch a digest of recent WhatsApp conversations from the linked browser session",
    ),
    ToolRegistration(
        tool_name="whatsapp.send_message",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Send a WhatsApp message through the linked browser session",
        diff_card_fields=["recipient", "recipient_identity", "content", "channel"],
    ),
    ToolRegistration(
        tool_name="whatsapp.session_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report whether a WhatsApp browser session is linked and how many messages are flaggable",
    ),
    ToolRegistration(
        tool_name="calendar.read_upcoming",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Fetch a digest of upcoming calendar events from the connected account",
    ),
    ToolRegistration(
        tool_name="calendar.digest_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report which calendar account is connected and its read scope",
    ),
    ToolRegistration(
        tool_name="cloud_files.list_recent",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="List recently modified cloud files (metadata only) from the connected account",
    ),
    ToolRegistration(
        tool_name="cloud_files.digest_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report which cloud-files account is connected and its read scope",
    ),
    ToolRegistration(
        tool_name="notes.list_recent",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="List recently edited notes and documents with summaries",
    ),
    ToolRegistration(
        tool_name="notes.digest_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report which notes account is connected and its read scope",
    ),
    ToolRegistration(
        tool_name="contacts.search",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="List or search contacts from the connected account",
    ),
    ToolRegistration(
        tool_name="contacts.digest_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report which contacts account is connected and its read scope",
    ),
    ToolRegistration(
        tool_name="bookmarks.search",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Search browser bookmarks from the local profile snapshot",
    ),
    ToolRegistration(
        tool_name="bookmarks.digest_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report which browser bookmark snapshot is connected",
    ),
    ToolRegistration(
        tool_name="history.recent",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Read recent visits from the local browser history profile",
    ),
    ToolRegistration(
        tool_name="history.digest_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Report which browser history file is connected",
    ),
    ToolRegistration(
        tool_name="telephony.prepare_call",
        tier=RiskTier.PREPARE,
        confirmation_required=False,
        description="Look up a contact and draft a talking-point brief + script (shown, never dialed)",
    ),
    ToolRegistration(
        tool_name="telephony.start_call",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Open the native dialer pre-filled for the recipient (v1 handoff, never an autonomous call)",
        diff_card_fields=["recipient", "recipient_identity", "phone", "script", "channel"],
    ),
    ToolRegistration(
        tool_name="reservations.search_slots",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Search available booking slots for a venue, date, and party size",
    ),
    ToolRegistration(
        tool_name="reservations.create",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Create a restaurant/appointment reservation",
        diff_card_fields=["venue", "at", "party_size", "guest_name", "slot_id", "channel"],
    ),
    ToolRegistration(
        tool_name="smart_home.device_status",
        tier=RiskTier.OBSERVE,
        confirmation_required=False,
        description="Read status from local smart-home devices (lights, cameras, sensors, energy)",
    ),
    ToolRegistration(
        tool_name="smart_home.control",
        tier=RiskTier.EXECUTE,
        confirmation_required=True,
        description="Control a non-critical smart-home device (lights, fans, AC, music, TV)",
        diff_card_fields=["device_id", "device_name", "category", "action", "value", "channel"],
    ),
]


def register_phase6_connector_tools() -> None:
    """Register the Phase 6 connector tools (observe-tier reads only)."""
    for tool in _PHASE6_CONNECTOR_TOOLS:
        if not PermissionEngine.is_registered(tool.tool_name):
            PermissionEngine.register(tool)
