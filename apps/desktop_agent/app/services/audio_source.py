"""
Audio capture (Phase 1).

A background sounddevice InputStream pushes 16kHz mono float32 frames into a
thread-safe deque. The voice loop pulls frames from it. `AudioSource` is the
injectable protocol, so tests use `ArrayAudioSource` without a sound card.
"""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


class MicrophoneSource:
    """Reads frames from the default microphone via sounddevice."""

    def __init__(self, sample_rate: int, frames_per_chunk: int) -> None:
        self.sample_rate = sample_rate
        self.frames_per_chunk = frames_per_chunk
        self._queue: deque[np.ndarray] = deque()
        self._stream = None

    def open(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        def callback(indata, _frames, _time, status) -> None:
            if status:
                logger.warning("mic callback status: %s", status)
            self._queue.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.frames_per_chunk,
            callback=callback,
        )
        self._stream.start()
        logger.info("microphone open @%dHz", self.sample_rate)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def read(self, max_: int = 512) -> list[np.ndarray]:
        frames = []
        while self._queue and len(frames) < max_:
            frames.append(self._queue.popleft())
        return frames


class ArraySourceInput:
    """Synthetic audio source for tests and offline replay of wav frames.

    Pops frames precomputed at 16kHz; raises on empty like a dead mic would
    return silence after end() is called.
    """

    def __init__(self, frames: list[np.ndarray]) -> None:
        self._frames = frames
        self.sample_rate = 16000
        self._ended = False

    def open(self) -> None:
        pass

    def close(self) -> None:
        self._ended = True

    def read(self, max_: int = 512) -> list[np.ndarray]:
        out, self._frames = self._frames[:max_], self._frames[max_:]
        return out

    @property
    def exhausted(self) -> bool:
        return not self._frames
