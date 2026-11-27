"""
Graceful Degradation (Phase 10.1).

Falls back to text-only interaction when audio subsystems (STT, TTS, wake word)
are unavailable or too slow. The agent should never just go silent — when voice
fails, text works.

Provides:
  - `DegradationMode`: tracks which subsystems are healthy and what fallback
    mode is active.
  - `TextFallback`: a simple text-in/text-out interface that bypasses audio
    entirely, using the same command pipeline.
  - Health checks for each subsystem with automatic mode switching.

Usage:
    mode = DegradationMode()
    mode.check_health(stt, tts, wake_detector)
    if mode.is_text_only:
        print("Voice unavailable, using text mode")
    response = TextFallback(mode, command_runner).handle("what time is it")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class SubsystemStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class InteractionMode(str, Enum):
    VOICE = "voice"  # full voice loop
    TEXT_ONLY = "text_only"  # text input/output only
    VOICE_BUT_DEGRADED = "voice_degraded"  # voice works but TTS fails — show text


@dataclass
class SubsystemHealth:
    """Health status of a single subsystem."""

    name: str
    status: SubsystemStatus = SubsystemStatus.HEALTHY
    last_check: float = 0.0  # timestamp of last health check
    error: str | None = None
    latency_ms: float = 0.0  # last check latency

    @property
    def is_usable(self) -> bool:
        return self.status != SubsystemStatus.UNAVAILABLE


@dataclass
class DegradationMode:
    """Tracks overall system health and determines interaction mode.

    Monitors STT, TTS, and wake-word subsystems. Automatically switches
    to text-only mode when voice is unavailable, and to degraded voice
    mode when TTS fails but STT still works.
    """

    stt: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("stt"))
    tts: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("tts"))
    wake: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("wake"))
    # Ollama is not required for voice: the planner has deterministic routes.
    # Its loss is reported as degraded, never as a reason to go silent.
    llm: SubsystemHealth = field(default_factory=lambda: SubsystemHealth("llm"))

    # Thresholds
    health_check_interval: float = 30.0  # seconds between checks
    max_latency_ms: float = 5000.0  # consider unhealthy if latency exceeds this

    @property
    def mode(self) -> InteractionMode:
        """Determine the current interaction mode based on subsystem health."""
        if self.stt.status == SubsystemStatus.UNAVAILABLE:
            return InteractionMode.TEXT_ONLY

        if any(
            sub.status == SubsystemStatus.UNAVAILABLE for sub in (self.tts, self.wake)
        ) or self.llm.status in (
            SubsystemStatus.UNAVAILABLE,
            SubsystemStatus.DEGRADED,
        ):
            # TTS/wake/Ollama loss — voice still works manually or with text
            # replies; deterministic planner routes cover the LLM gap.
            return InteractionMode.VOICE_BUT_DEGRADED

        return InteractionMode.VOICE

    @property
    def is_text_only(self) -> bool:
        return self.mode == InteractionMode.TEXT_ONLY

    @property
    def is_degraded(self) -> bool:
        return self.mode != InteractionMode.VOICE

    def check_stt(self, stt: Any, probe: Callable[[], bool] | None = None) -> SubsystemHealth:
        """Check STT health. `probe` verifies the local backend is importable."""
        now = time.time()
        if now - self.stt.last_check < self.health_check_interval:
            return self.stt

        start = time.monotonic()
        try:
            if probe is not None and not probe():
                self.stt.status = SubsystemStatus.UNAVAILABLE
                self.stt.error = "Local STT backend is not available"
            elif (
                stt is None
                or hasattr(stt, "model")
                or hasattr(stt, "_model")
                or hasattr(stt, "transcribe")
            ):
                self.stt.status = SubsystemStatus.HEALTHY
                self.stt.error = None
            else:
                self.stt.status = SubsystemStatus.UNAVAILABLE
                self.stt.error = "No transcribe method"
        except Exception as exc:
            self.stt.status = SubsystemStatus.UNAVAILABLE
            self.stt.error = str(exc)

        self.stt.latency_ms = (time.monotonic() - start) * 1000
        self.stt.last_check = now
        return self.stt

    def check_tts(self, tts: Any, probe: Callable[[], bool] | None = None) -> SubsystemHealth:
        """Check TTS health by verifying model availability."""
        now = time.time()
        if now - self.tts.last_check < self.health_check_interval:
            return self.tts

        start = time.monotonic()
        try:
            if probe is not None and not probe():
                self.tts.status = SubsystemStatus.UNAVAILABLE
                self.tts.error = "Piper voice model is not available"
            elif (
                tts is None
                or hasattr(tts, "synthesize")
                or hasattr(tts, "synthesize_play")
            ):
                self.tts.status = SubsystemStatus.HEALTHY
                self.tts.error = None
            else:
                self.tts.status = SubsystemStatus.UNAVAILABLE
                self.tts.error = "No synthesize method"
        except Exception as exc:
            self.tts.status = SubsystemStatus.UNAVAILABLE
            self.tts.error = str(exc)

        self.tts.latency_ms = (time.monotonic() - start) * 1000
        self.tts.last_check = now
        return self.tts

    def check_wake(self, wake: Any, probe: Callable[[], bool] | None = None) -> SubsystemHealth:
        """Check wake word detector health."""
        now = time.time()
        if now - self.wake.last_check < self.health_check_interval:
            return self.wake

        start = time.monotonic()
        try:
            if probe is not None and not probe():
                self.wake.status = SubsystemStatus.DEGRADED
                self.wake.error = "Wake model unavailable — clap/manual trigger only"
            elif (
                wake is None
                or hasattr(wake, "score_frame")
                or hasattr(wake, "is_activated")
            ):
                self.wake.status = SubsystemStatus.HEALTHY
                self.wake.error = None
            else:
                self.wake.status = SubsystemStatus.DEGRADED
                self.wake.error = "Clap-only mode"
        except Exception as exc:
            self.wake.status = SubsystemStatus.DEGRADED
            self.wake.error = str(exc)

        self.wake.latency_ms = (time.monotonic() - start) * 1000
        self.wake.last_check = now
        return self.wake

    def check_llm(self, probe: Callable[[], bool] | None = None) -> SubsystemHealth:
        """Check whether the local planner is reachable.

        Unavailability is reported as DEGRADED, not UNAVAILABLE: the planner's
        deterministic routes still handle common requests offline.
        """
        now = time.time()
        if now - self.llm.last_check < self.health_check_interval:
            return self.llm

        start = time.monotonic()
        try:
            available = True if probe is None else bool(probe())
            if available:
                self.llm.status = SubsystemStatus.HEALTHY
                self.llm.error = None
            else:
                self.llm.status = SubsystemStatus.DEGRADED
                self.llm.error = "Ollama unreachable — deterministic routes only"
        except Exception as exc:
            self.llm.status = SubsystemStatus.DEGRADED
            self.llm.error = str(exc)

        self.llm.latency_ms = (time.monotonic() - start) * 1000
        self.llm.last_check = now
        return self.llm

    def check_all(
        self,
        stt: Any = None,
        tts: Any = None,
        wake: Any = None,
        probes: dict[str, Callable[[], bool]] | None = None,
    ) -> InteractionMode:
        """Run health checks on all subsystems and return the resulting mode."""
        probes = probes or {}
        if stt or "stt" in probes:
            self.check_stt(stt, probes.get("stt"))
        if tts or "tts" in probes:
            self.check_tts(tts, probes.get("tts"))
        if wake or "wake" in probes:
            self.check_wake(wake, probes.get("wake"))
        self.check_llm(probes.get("llm"))

        mode = self.mode
        if mode != InteractionMode.VOICE:
            logger.warning(
                "System degraded: mode=%s, stt=%s, tts=%s, wake=%s, llm=%s",
                mode.value,
                self.stt.status.value,
                self.tts.status.value,
                self.wake.status.value,
                self.llm.status.value,
            )
        return mode

    def summary(self) -> dict:
        """Return a summary of all subsystem health."""
        return {
            "mode": self.mode.value,
            "stt": {
                "status": self.stt.status.value,
                "error": self.stt.error,
                "latency_ms": self.stt.latency_ms,
            },
            "tts": {
                "status": self.tts.status.value,
                "error": self.tts.error,
                "latency_ms": self.tts.latency_ms,
            },
            "wake": {
                "status": self.wake.status.value,
                "error": self.wake.error,
                "latency_ms": self.wake.latency_ms,
            },
            "llm": {
                "status": self.llm.status.value,
                "error": self.llm.error,
                "latency_ms": self.llm.latency_ms,
            },
        }


@dataclass
class TextFallback:
    """Text-only fallback interface when voice is unavailable.

    Uses the same command pipeline as the voice loop — parses, permission-gates,
    executes, and returns the result as text. No audio involved.
    """

    mode: DegradationMode
    command_runner: Any = None  # command_runner.run_command or equivalent
    store: Any = None  # TaskStore for persistence

    def handle(self, text: str) -> dict:
        """Process a text command and return the result.

        Returns a dict with:
          - reply: text response to show the user
          - tool: which tool was used (if any)
          - status: "ok", "blocked", "error"
          - mode: current interaction mode
        """
        if not text or not text.strip():
            return {
                "reply": "No input provided.",
                "tool": None,
                "status": "error",
                "mode": self.mode.mode.value,
            }

        if self.command_runner:
            try:
                result = self.command_runner(text)
                return {
                    "reply": result.get("reply", ""),
                    "tool": result.get("tool"),
                    "status": result.get("status", "ok"),
                    "mode": self.mode.mode.value,
                }
            except Exception as exc:
                logger.warning("Text fallback command failed: %s", exc)
                return {
                    "reply": f"Error: {exc}",
                    "tool": None,
                    "status": "error",
                    "mode": self.mode.mode.value,
                }

        # No command runner — return a basic acknowledgment
        return {
            "reply": f"[Text mode] Received: {text}",
            "tool": None,
            "status": "ok",
            "mode": self.mode.mode.value,
        }

    @property
    def is_active(self) -> bool:
        """Whether text fallback is currently the active mode."""
        return self.mode.is_text_only
