"""
Speech-to-text (Phase 1).

Local Whisper via faster-whisper (CT2). Audio is passed in as a 16kHz mono
numpy float32 array; the model is loaded lazily so tests that never transcribe
don't pay the load cost. Transcription never leaves the laptop.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class WhisperSTT:
    def __init__(
        self, model: str = "base", device: str = "cpu", compute_type: str = "int8"
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # deferred: heavy import

        logger.info(
            "loading Whisper '%s' (%s/%s)...", self.model_name, self.device, self.compute_type
        )
        t0 = time.monotonic()
        self._model = WhisperModel(
            self.model_name, device=self.device, compute_type=self.compute_type
        )
        logger.info("whisper ready in %.1fs", time.monotonic() - t0)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        """Transcribe 16kHz mono float32 (-1..1) samples; returns text, stripped."""
        self._load()
        if audio is None or len(audio) == 0:
            return ""
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


def save_wav(path: str | Path, audio: np.ndarray, sample_rate: int) -> None:
    """Write float32 mono audio to a standard 16-bit PCM wav file."""
    import wave

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
