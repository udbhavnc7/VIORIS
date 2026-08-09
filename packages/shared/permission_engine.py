"""
Vioris Permission Engine — Static Risk Registry.

A tool's risk tier is a property of the TOOL, registered at build time.
The LLM proposes plans; it never sets or reclassifies risk levels.
Unregistered tools are BLOCKED, not defaulted to observe.

See docs/03-permissions-and-security.md for the full permission matrix.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from packages.shared.schemas import PermissionResult, RiskTier, ToolRegistration

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

    @classmethod
    def classify(cls, tool_name: str) -> PermissionResult:
        """Look up a tool's risk classification.

        Returns a PermissionResult with tier, confirmation requirements, etc.
        Raises UnknownToolError if the tool isn't registered — unregistered = blocked.
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
