"""
Chunking (Phase 4, Prompt 4.2).

Deterministically splits long text into overlapping, sentence-aware chunks that
carry character offsets so retrieval can cite the page/location it answered
from. Same text in → same chunks out (ingestion is idempotent).
"""

from __future__ import annotations

import re

from dataclasses import dataclass

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    text: str
    seq: int
    start: int  # char offset into the document
    end: int  # exclusive
    page: int  # 1-based source page; 0 = unknown


class Chunker:
    """Chunk text into sentence-oriented segments of at most ``max_chars``."""

    def __init__(self, max_chars: int = 900, overlap: int = 120) -> None:
        if max_chars <= overlap:
            raise ValueError("max_chars must exceed overlap")
        self.max_chars = max_chars
        self.overlap = overlap

    def chunk_text(self, text: str, page: int = 0) -> list[Chunk]:
        """Split a single page's text into chunks with char offsets."""
        if not text.strip():
            return []
        sentences = self._split(text)
        if not sentences:
            return [Chunk(text=text.strip(), seq=0, start=0, end=len(text), page=page)]
        # Split over-length sentences into fixed windows so a single giant
        # sentence (common in pasted paragraphs) still yields multiple chunks.
        units: list[str] = []
        for sent in sentences:
            if len(sent) > self.max_chars:
                units.extend(self._windows(sent))
            else:
                units.append(sent)
        return self._group(text, units, page)

    def _windows(self, sentence: str) -> list[str]:
        step = self.max_chars - self.overlap
        return [
            sentence[i : i + self.max_chars]
            for i in range(0, len(sentence), max(1, step))
            if sentence[i : i + self.max_chars].strip()
        ]

    def chunk_pages(self, pages: list[str]) -> list[Chunk]:
        """Chunk a whole document given its page texts (index 0 = page 1)."""
        chunks: list[Chunk] = []
        for index, page_text in enumerate(pages, start=1):
            for ch in self.chunk_text(page_text, page=index):
                chunks.append(ch)
        return chunks

    # ── internals ──────────────────────────────────────────────────────────
    def _split(self, text: str) -> list[str] | None:
        return [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]

    def _group(self, text: str, sentences: list[str], page: int) -> list[Chunk]:
        chunks: list[Chunk] = []
        seq = 0
        pos = 0
        current: list[str] = []
        current_start = 0

        for sentence in sentences:
            start = text.find(sentence, pos)
            if start == -1:
                start = pos
            if current:
                projected = len(" ".join(current)) + 1 + len(sentence)
                if projected > self.max_chars:
                    body = " ".join(current)
                    end = start
                    chunks.append(Chunk(text=body, seq=seq, start=current_start, end=end, page=page))
                    seq += 1
                    # keep the tail (overlap) of the previous chunk as context
                    tail = body[-self.overlap :]
                    current = list(tail.split(" "))
                    if current and current[0] == "":
                        current = current[1:]
                    current_start = end - len(tail)
            current.append(sentence)
            pos = start + len(sentence)

        if current:
            body = " ".join(current)
            chunks.append(Chunk(text=body, seq=seq, start=current_start, end=len(text), page=page))
        return chunks