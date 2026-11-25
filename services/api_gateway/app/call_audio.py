"""Call audio buffering for bidirectional WS audio (Phase 8.5, 10b).

Small value object: the phone streams 16kHz mono float32 PCM chunks over the
paired socket; the gateway accumulates them until the user finishes speaking
and the buffer drains into Whisper.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 16000


class CallAudioBuffer:
    """Accumulates PCM chunks per call."""

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE, silence_threshold: float = 0.01) -> None:
        self.sample_rate = sample_rate
        self._silence_threshold = silence_threshold
        self._chunks: list[np.ndarray] = []

    @property
    def duration(self) -> float:
        return sum(len(c) for c in self._chunks) / self.sample_rate

    def push(self, audio: np.ndarray) -> None:
        if audio.size:
            self._chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1))

    def has_speech(self) -> bool:
        return bool(self._chunks)

    def drain(self) -> np.ndarray:
        chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def tail_is_silent(self, tail_seconds: float = 0.35) -> bool:
        """True if the last `tail_seconds` of audio is near-silent (user done)."""
        if not self._chunks:
            return False
        audio = np.concatenate(self._chunks)
        n = max(0, int(tail_seconds * self.sample_rate))
        tail_audio = audio[-n:] if n else audio
        if tail_audio.size == 0:
            return False
        return bool(float(np.sqrt(np.mean(tail_audio ** 2))) < self._silence_threshold)


def pcm16_bytes_to_float32(data: bytes) -> np.ndarray:
    """Decode base64 PCM16 mono to a float32 array (values in -1..1)."""
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def float32_to_pcm16_bytes(audio: np.ndarray) -> bytes:
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()


def base64_decode(data: str) -> Optional[np.ndarray]:
    import base64

    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as exc:
        logger.warning("Audio chunk decode failed: %s", exc)
        return None
    return pcm16_bytes_to_float32(raw)
