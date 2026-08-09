"""
Wake-word detection (Phase 1).

Two activation paths, both fully local:
  1. openWakeWord model (built-in 'hey_jarvis'; a custom 'vioris' model slots
     in by passing its path as `wake_word_model`). Runs in onnxruntime mode so
     no tflite-runtime is needed.
  2. Clap pattern — a simple energy-spike detector: N claps within a time
     window. Intended as the secondary activation from the START prompting flow.

The detector consumes 16kHz mono float32 audio in short frames and returns
activation booleans per frame. Detection is CPU-only and never uploads audio.
"""

from __future__ import annotations

import logging
import time
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


class ClapDetector:
    """Detects `required` energy spikes (claps) inside a rolling time window."""

    def __init__(
        self,
        required: int,
        window_seconds: float = 2.5,
        energy_threshold: float = 0.2,
    ) -> None:
        self.required = required
        self.window_seconds = window_seconds
        self.energy_threshold = energy_threshold
        self._timestamps: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._timestamps and now - self._timestamps[0] > self.window_seconds:
            self._timestamps.popleft()

    def _is_clap(self, frame: np.ndarray) -> bool:
        if frame.size == 0:
            return False
        rms = np.sqrt(np.mean(frame.astype(np.float64) ** 2))
        return bool(rms > self.energy_threshold)  # a clap spikes far above speech

    def detect(self, frame: np.ndarray) -> bool:
        """Feed one audio frame. Returns True once the clap pattern completes."""
        now = time.monotonic()
        if self._is_clap(frame):
            self._timestamps.append(now)
        # Prune before checking so the window stays strict.
        self._prune(now)
        if len(self._timestamps) < self.required:
            return False
        finished = now - self._timestamps[0] <= self.window_seconds
        if finished:
            self._timestamps.clear()
        return finished


class WakeWordDetector:
    """Wrapper around an openWakeWord onnx model."""

    def __init__(self, model_name: str, threshold: float = 0.5) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self._model = None
        self._frame_samples = 1280  # 80 ms @ 16kHz, openWakeWord's unit

    def load(self) -> None:
        if self._model is not None:
            return
        from openwakeword.model import Model  # deferred: heavy import

        self._model = Model(
            wakeword_models=[self.model_name],
            inference_framework="onnx",
        )
        logger.info("wake word model '%s' loaded (onnx)", self.model_name)

    def score_frame(self, frame: np.ndarray) -> float:
        """Return the model's activation score for one 16kHz frame."""
        if self._model is None:
            raise RuntimeError("WakeWordDetector not loaded; call load() first")
        result = self._model.predict(frame)
        return float(result.get(self.model_name, 0.0))

    def is_activated(self, frame: np.ndarray) -> bool:
        return self.score_frame(frame) >= self.threshold

    @property
    def frame_size(self) -> int:
        return self._frame_samples
