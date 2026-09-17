"""
Voiceprint verification (Phase 8, Prompt 8.1).

Local speaker-embedding model for second-factor verification on Critical-tier
actions. Uses Resemblyzer for lightweight, CPU-only speaker verification — no
paid APIs, no network calls, no data leaves the laptop.

Flow:
  1. Enrollment: user speaks a passphrase; multiple audio segments are embedded
     and averaged into a reference embedding stored on disk.
  2. Verification: a new audio segment is embedded and compared against the
     reference. If cosine similarity >= threshold, the speaker is verified.

The permission engine calls `verify_voiceprint()` as its second-factor hook
for Critical-tier actions. If no enrollment exists, verification fails closed
(requires enrollment before any Critical action can pass).
"""

from __future__ import annotations

import logging
import struct
import wave
from pathlib import Path
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)

# Similarity threshold: below this, the voice is not a match.
# 0.70 is a reasonable starting point — tune after real-world testing.
DEFAULT_THRESHOLD = 0.70

# Number of audio segments averaged during enrollment for a stable reference.
ENROLLMENT_SAMPLES = 3


class SpeakerEncoder(Protocol):
    """Interface for the underlying speaker-embedding model."""

    def embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray: ...


class ResemblyzerEncoder:
    """Resemblyzer-based speaker encoder. Lazy import — only loaded when first
    instantiated, so the rest of the app is unaffected if resemblyzer isn't
    installed."""

    def __init__(self) -> None:
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        from resemblyzer import precompute_wav_feats  # noqa: F811 — deferred import

        self._precompute_wav_feats = precompute_wav_feats  # type: ignore[attr-defined]
        self._loaded = True
        logger.info("Resemblyzer speaker encoder loaded")

    def embed(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Compute a 256-dim speaker embedding from a raw audio signal."""
        self._load()
        # Resemblyzer expects float32 in [-1, 1], 16kHz mono
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        # Reshape for resemblyzer: expects (1, num_samples)
        audio_2d = audio.reshape(1, -1)
        embedding = self._precompute_wav_feats(audio_2d)  # type: ignore[operator]
        return embedding.flatten()[:256]  # take first 256 dims for consistency


class FakeEncoder:
    """Deterministic bag-of-words encoder for tests. Produces embeddings from
    audio energy profile — not speaker-aware, but enough to test the pipeline."""

    def embed(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        rng = np.random.RandomState(42)
        base = rng.randn(256).astype(np.float32)
        energy = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size > 0 else 0.0
        return base + energy * 0.1


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors. Returns 0.0 for zero vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


class VoiceprintManager:
    """Manages enrollment and verification for a single user.

    Stores the reference embedding as a .npz file on disk so it persists
    across sessions. The file path is configurable (defaults to
    ~/.vioris/voiceprint.npz).
    """

    def __init__(
        self,
        encoder: SpeakerEncoder | None = None,
        store_path: Path | str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self.encoder = encoder or ResemblyzerEncoder()
        self.threshold = threshold
        if store_path is None:
            from pathlib import Path as _Path

            store_path = _Path.home() / ".vioris" / "voiceprint.npz"
        self.store_path = Path(store_path)
        self._reference: np.ndarray | None = None

    # ── enrollment ─────────────────────────────────────────────────────────

    def enroll(self, audio_segments: list[np.ndarray], sample_rate: int = 16000) -> np.ndarray:
        """Enroll from multiple audio segments. Returns the averaged reference
        embedding. Persists to disk immediately."""
        if not audio_segments:
            raise ValueError("need at least one audio segment for enrollment")
        embeddings = [self.encoder.embed(seg, sample_rate) for seg in audio_segments]
        ref = np.mean(embeddings, axis=0).astype(np.float32)
        self._reference = ref
        self._save()
        logger.info("voiceprint enrolled (%d segments, ref shape=%s)", len(audio_segments), ref.shape)
        return ref

    def enroll_single(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Convenience: enroll from a single segment."""
        return self.enroll([audio], sample_rate)

    # ── verification ───────────────────────────────────────────────────────

    def verify(self, audio: np.ndarray, sample_rate: int = 16000) -> tuple[bool, float]:
        """Verify a voice sample against the enrolled reference.

        Returns (passed: bool, similarity: float).
        Fails closed if no enrollment exists.
        """
        ref = self._load_or_none()
        if ref is None:
            logger.warning("voiceprint verification failed: no enrollment")
            return False, 0.0

        embedding = self.encoder.embed(audio, sample_rate)
        sim = cosine_similarity(embedding, ref)
        passed = sim >= self.threshold
        logger.info(
            "voiceprint verification: similarity=%.4f, threshold=%.4f, passed=%s",
            sim,
            self.threshold,
            passed,
        )
        return passed, sim

    def is_enrolled(self) -> bool:
        """True if a reference embedding exists on disk."""
        return self.store_path.exists()

    # ── persistence ────────────────────────────────────────────────────────

    def _load_or_none(self) -> np.ndarray | None:
        if self._reference is not None:
            return self._reference
        if not self.store_path.exists():
            return None
        data = np.load(self.store_path)
        self._reference = data["reference"]
        return self._reference

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self.store_path, reference=self._reference)
        logger.info("voiceprint saved to %s", self.store_path)

    def delete(self) -> bool:
        """Remove the enrolled voiceprint. Returns True if one existed."""
        if self.store_path.exists():
            self.store_path.unlink()
            self._reference = None
            logger.info("voiceprint deleted")
            return True
        return False


def load_wav_mono_float32(path: Path | str) -> tuple[np.ndarray, int]:
    """Load a WAV file and return (audio: float32 mono, sample_rate)."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported WAV sample width: {sampwidth} bytes")

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels)[:, 0]  # take first channel

    return samples, sr
