"""
Voice loop orchestrator (Phase 1).

Runs the idle → listening → thinking → speaking state machine against an
injectable audio source.

Flow:
  idle       → Watch wake-word + clap. On activation: record utterance.
  listening  → Record until endpoint (silence or timeout) or interrupt.
  thinking   → Transcribe (Whisper), handle the command, form a reply.
  speaking   → Synthesize (Piper) and play chunk-by-chunk, checking the hard
               interrupt between chunks. Then back to idle.

"stop" / "vioris stop" sets the state machine's stop event; playback polls it,
giving <500ms cancel latency. Everything is injectable so tests run on
synthetic audio with no sound card.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from packages.shared.audit import compute_chain
from packages.shared.schemas import AuditEvent

from ..commands import handle as handle_command
from .state_machine import AgentStateMachine
from .tts import TTSUnavailableError
from .wake_detector import ClapDetector, WakeWordDetector

logger = logging.getLogger(__name__)

ENDPOINT_SILENCE_SECONDS = 1.0
GENESIS = "0" * 64


class VoiceLoop:
    def __init__(
        self,
        state_machine: AgentStateMachine,
        wake: WakeWordDetector,
        clap: ClapDetector,
        tts,
        stt,
        source,
        config,
        executor=None,
        store=None,
    ) -> None:
        self.state = state_machine
        self.wake = wake
        self.clap = clap
        self.tts = tts
        self.stt = stt
        self.source = source
        self.cfg = config
        self.executor = executor
        self.store = store
        self.responses: list[tuple[str, str | None]] = []  # (transcript, reply)
        self.events: list[AuditEvent] = []

    def run_forever(self) -> None:
        """Blocking main loop: wake-wait forever."""
        self.source.open()
        self.wake.load()
        logger.info(
            "voice loop running; wake via '%s' or %d claps",
            self.cfg.wake_phrase,
            self.cfg.claps_required,
        )
        try:
            while True:
                self._idle_wait()
        finally:
            self.source.close()

    # ── idle: hunt for activation ────────────────────────────────────────
    def _idle_wait(self) -> None:
        self.state.to_idle()
        self.state.clear_stop()
        window = np.zeros(0, dtype=np.float32)
        while not self.state.interrupt_raised():
            frames = self.source.read(max_=2)
            if not frames:
                # Finite test sources end; live microphones never do.
                if getattr(self.source, "exhausted", False):
                    return
                time.sleep(0.01)
                continue
            for frame in frames:
                audio = frame[:, 0] if frame.ndim > 1 else frame
                window = np.concatenate([window, audio])
                while len(window) >= self.wake.frame_size:
                    chunk = window[: self.wake.frame_size]
                    window = window[self.wake.frame_size :]
                    if self.wake.is_activated(chunk):
                        self._on_wake("wake-word")
                        return
                    if self.clap.detect(chunk):
                        self._on_wake("clap")
                        return
            # keep the window bounded (a few seconds) so frames keep flowing
            window = window[-int(self.cfg.sample_rate * 4) :]

    def _on_wake(self, method: str) -> None:
        self._record_and_respond(method)

    # ── active round ─────────────────────────────────────────────────────
    def _record_and_respond(self, method: str) -> None:
        self.state.to_listening()
        self._audit("wake", "wake_activated", {"method": method})
        audio = self._record_utterance()
        self.state.to_thinking()
        self.state.acknowledge_stop()

        if audio.size < self.cfg.sample_rate * 0.2:  # too short to be a command
            self.state.to_idle()
            return

        try:
            transcript = self.stt.transcribe(audio)
        except Exception as exc:  # noqa: BLE001 — keep the loop alive on STT failure
            logger.exception("STT failed; %s", exc)
            transcript = ""
        logger.info("heard: %r", transcript)
        if not transcript:
            self.state.to_idle()
            return

        result = handle_command(transcript)
        self._audit("user", result.action, result.detail)
        self.responses.append((transcript, result.reply))
        self._execute_and_record(transcript, result)
        self._speak(result.reply)

    def _record_utterance(self) -> np.ndarray:
        """Record until endpoint (silence or timeout) or interrupt."""
        buf = np.zeros(0, dtype=np.float32)
        silence = 0.0
        last_voice = time.monotonic()
        while not self.state.interrupt_raised():
            frames = self.source.read()
            if not frames:
                if time.monotonic() - last_voice > self.cfg.capture_timeout_seconds:
                    break
                time.sleep(0.01)
                continue
            for frame in frames:
                audio = frame[:, 0] if frame.ndim > 1 else frame
                buf = np.concatenate([buf, audio])
                if self._voice_level(audio) < 0.005:
                    silence += self.cfg.chunk_ms / 1000
                else:
                    silence = 0.0
                    last_voice = time.monotonic()
                if self.state.interrupt_raised():
                    break
                if silence >= ENDPOINT_SILENCE_SECONDS:
                    break
        return buf

    @staticmethod
    def _voice_level(frame: np.ndarray) -> float:
        if frame.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))

    def _speak(self, reply: str | None) -> None:
        if not reply:
            self.state.to_idle()
            return
        if self.state.interrupt_raised():
            return  # stay in STOPPED; hard interrupt already handled it
        self.state.to_speaking()
        try:
            self.tts.synthesize_play(reply, interrupt_check=lambda: self.state.interrupt_raised())
        except Exception as exc:  # noqa: BLE001 — keep the loop alive on TTS failure
            if not isinstance(exc, TTSUnavailableError):
                logger.exception("TTS failure; continuing")
            else:
                logger.warning("TTS unavailable; replying silently: %s", exc)
        self.state.to_idle()

    # ── execution + persistence ────────────────────────────────────────────
    def _execute_and_record(self, transcript: str, result) -> None:
        """Gate the command through the shared runner (permission engine ->
        executor -> store). In-memory audit events mirror what the store saw."""
        if self.executor is None:
            if self.store is not None:
                from ..command_runner import run_command

                run_command(transcript, self.executor or None, self.store)
            return

        from ..command_runner import run_command

        outcome = run_command(transcript, self.executor, self.store)

        if outcome.get("blocked"):
            self._audit(
                "system", "requested_approval", {"tool": result.tool, "why": outcome["blocked"]}
            )
        else:
            self._audit(
                "system", "executed", {"tool": result.tool, "outcome": outcome.get("outcome")}
            )

    # ── audit chain ──────────────────────────────────────────────────────
    def _audit(self, actor: str, action: str, detail: dict, task_id: str | None = None) -> None:
        event = AuditEvent(actor=actor, action=action, detail=detail, task_id=task_id)
        prev = self.events[-1].hash if self.events else GENESIS
        compute_chain([event], genesis_hash=prev)
        self.events.append(event)
        logger.info("audit %s: %s", actor, action)
