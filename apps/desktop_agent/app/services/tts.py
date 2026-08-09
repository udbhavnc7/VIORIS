"""
Text-to-speech (Phase 1).

Local Piper. `synthesize(text)` yields (audio:int16 numpy, sample_rate) chunks
one sentence at a time. Playback is delegated to `sounddevice` so a hard
interrupt can stop between chunks (checked by the caller). Nothing is uploaded.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class TTSUnavailableError(Exception):
    """Raised when no Piper voice model is configured/loadable."""


def find_piper_voice(model: str | Path, search_dirs: list[Path]) -> Path | None:
    """Locate a Piper onnx voice by name or path.

    Accepts a full path, a bare voice name, or model arg passed as
    'name:ext'. Checks search_dirs for '<name>.onnx'. Returns None if unset.
    """
    if not model:
        return None
    candidate = Path(str(model))
    if candidate.suffix == ".onnx" and candidate.exists():
        return candidate
    # looks like a bare name — try "<name>.onnx" in each search dir
    dotted = candidate if candidate.suffix else Path(str(candidate) + ".onnx")
    for d in search_dirs:
        p = d / dotted.name if not dotted.is_absolute() else dotted
        if p.exists():
            return p
    for d in search_dirs:
        p = d / f"{model}.onnx"
        if p.exists():
            return p
    return None


class PiperTTS:
    """Thin wrapper over piper's PiperVoice.load + synthesize."""

    def __init__(self, model_path: str | Path | None = None) -> None:
        self.model_path = Path(model_path) if model_path else None
        self._voice = None

    def _load(self) -> None:
        if self._voice is not None:
            return
        if self.model_path is None or not self.model_path.exists():
            raise TTSUnavailableError(
                f"Piper voice model not found at '{self.model_path}'. "
                f"Set TTS_MODEL or drop <voice>.onnx into models/."
            )
        from piper import PiperVoice

        logger.info("loading piper voice '%s'...", self.model_path)
        self._voice = PiperVoice.load(self.model_path)

    def synthesize(self, text: str):
        """Yield (int16 mono np array, sample_rate) for each sentence."""
        self._load()
        for chunk in self._voice.synthesize(text):
            arr = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16).copy()
            if arr.size:
                yield arr, chunk.sample_rate

    def synthesize_play(self, text: str, interrupt_check=lambda: False) -> bool:
        """Synthesize and play; stop between chunks if interrupt_check() fires.

        Returns True if the full reply played, False if interrupted partway.
        This is the hard-interrupt path for 'stop' mid-speech (docs/03 §5).
        """
        player = AudioPlayer()
        for audio, rate in self.synthesize(text):
            if interrupt_check():
                return False
            if not player.play(audio, rate, interrupt_check=interrupt_check):
                return False
        return True


class AudioPlayer:
    """Plays int16 audio via sounddevice; polls interrupt during playback.

    Playback runs in a worker thread so a stop request can call sd.stop()
    quickly; the play() loop polls `interrupt_check` every ~40ms and aborts.
    """

    def __init__(self, sample_rate: int = 22050) -> None:
        self.sample_rate = sample_rate

    def play(
        self,
        audio: np.ndarray,
        sample_rate: int | None = None,
        interrupt_check=lambda: False,
    ) -> bool:
        """Play one buffer; returns False if interrupted mid-play."""
        if audio.size == 0:
            return not interrupt_check()
        try:
            import sounddevice as sd

            sd.play(audio, samplerate=sample_rate or self.sample_rate)
            while sd.get_stream() is not None and sd.get_stream().active:
                if interrupt_check():
                    sd.stop()
                    return False
                time.sleep(0.04)
            return not interrupt_check()
        except Exception as exc:  # noqa: BLE001 — no audio device on CI
            logger.warning("audio playback failed (no device?): %s", exc)
            return not interrupt_check()  # treat silence as success
