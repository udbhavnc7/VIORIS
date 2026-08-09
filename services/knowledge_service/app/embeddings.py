"""
Embeddings (Phase 4, Prompt 4.2).

Injectable embedder for RAG. Default backend is the local Ollama HTTP API
(cost $0 — never a paid embedding API). Tests use a deterministic FakeEmbedder
whose cosine similarity actually correlates with shared vocabulary, so
threshold behavior is exercised without a network/model.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from collections import Counter
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

_EMBED_VECTOR_WIDTH = 768


class EmbedderError(Exception):
    """The embedding backend failed."""


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    """Local embeddings via Ollama /api/embed. Cost $0."""

    def __init__(self, model: str | None = None, base_url: str | None = None, timeout: float = 60.0) -> None:
        self.model = model or os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.timeout = timeout
        self.dim = _EMBED_VECTOR_WIDTH

    def embed(self, text: str) -> list[float]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": text},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise EmbedderError(f"Ollama embedding failed: {exc}") from exc
        embeddings = (data.get("embeddings") or [data.get("embedding")]) or []
        if not embeddings:
            raise EmbedderError("Ollama returned no embedding")
        vec = list(embeddings[0])
        self.dim = len(vec)
        return vec


class FakeEmbedder:
    """Deterministic bag-of-words embedding for tests. Cosine similarity
    between two texts is high iff they share vocabulary — close enough to
    exercise the threshold + citation path without a real model."""

    dim = _EMBED_VECTOR_WIDTH

    def __init__(self, dim: int = _EMBED_VECTOR_WIDTH) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        counts = Counter(re.sub(r"[^\w\s]", "", text.lower()).split())
        for word, n in counts.items():
            h = int(hashlib.sha256(word.encode()).hexdigest(), 16)
            idx = h % self.dim
            vec[idx] += n
        norm = math.sqrt(sum(v * v for v in vec)) or 1e-9
        return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors. NaN-safe for zero vectors."""
    if len(a) != len(b):
        raise ValueError("embedding dimension mismatch")
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    noma = math.sqrt(sum(x * x for x in a))
    nomb = math.sqrt(sum(y * y for y in b))
    if noma == 0 or nomb == 0:
        return 0.0
    return dot / (noma * nomb)
