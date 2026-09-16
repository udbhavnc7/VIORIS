"""
Knowledge vector store (Phase 4, Prompt 4.2).

SQLite-backed mirror of the pgvector design: a `documents` table plus a `chunks`
table storing each chunk's text, citation offsets, page, and embedding as a
float JSON blob. Similarity is computed in Python (numpy-free) so the whole
pipeline runs on a laptop at $0; swapping to Postgres + a real cosine index in
a later phase keeps the same schema/layout.

Approval boundary: only documents whose path is inside the configured approved
sources are ever indexed. There is no "just this once" escape hatch.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .chunker import Chunker
from .embeddings import Embedder, cosine_similarity

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    source_path  TEXT NOT NULL,          -- the approved file it came from
    source_type  TEXT NOT NULL,          -- pdf / markdown / note / txt
    pages        INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    start_char  INTEGER NOT NULL,
    end_char    INTEGER NOT NULL,
    page        INTEGER NOT NULL DEFAULT 0,
    embedding   TEXT NOT NULL,           -- JSON float list
    source_path TEXT NOT NULL             -- denormalized for cheap citation
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
"""


@dataclass
class RetrievedChunk:
    text: str
    score: float
    doc_id: str
    title: str
    source_path: str
    page: int
    seq: int
    start_char: int = 0
    end_char: int = 0


@dataclass
class IndexResult:
    doc_id: str
    chunks_indexed: int
    ok: bool = True
    error: str | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class QueryResult:
    answer: str
    found: bool
    citation: str | None = None
    chunks: list[RetrievedChunk] = field(default_factory=list)
    below_threshold: bool = False
    detail: dict = field(default_factory=dict)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class VectorStore:
    """Storage + retrieval for indexed knowledge chunks."""

    def __init__(self, db_path: str | Path, embedder: Embedder | None = None, threshold: float = 0.25) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.embedder = embedder
        self.threshold = threshold
        self.chunker = Chunker()

    def close(self) -> None:  # pragma: no cover - teardown path
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ── ingestion ──────────────────────────────────────────────────────────
    def index(
        self,
        title: str,
        pages: list[str],
        source_path: str,
        source_type: str,
    ) -> IndexResult:
        """Chunk and embed one approved document. Idempotent per doc_id."""
        chunks = self.chunker.chunk_pages(pages)
        if not chunks:
            return IndexResult(doc_id="", chunks_indexed=0, ok=False, error="no text extracted")

        if self.embedder is None:
            return IndexResult(doc_id="", chunks_indexed=0, ok=False, error="no embedder configured")

        doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        now = _now()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO documents (doc_id, title, source_path, source_type, pages, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (doc_id, title, source_path, source_type, len(pages), now),
                )
                for ch in chunks:
                    vec = self.embedder.embed(ch.text)
                    self._conn.execute(
                        "INSERT INTO chunks (chunk_id, doc_id, seq, text, start_char, end_char, page, embedding, source_path) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            f"ch_{uuid.uuid4().hex[:10]}",
                            doc_id,
                            ch.seq,
                            ch.text,
                            ch.start,
                            ch.end,
                            ch.page,
                            json.dumps(vec),
                            source_path,
                        ),
                    )
                self._conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to index document: %s", exc)
                self._conn.rollback()
                return IndexResult(doc_id="", chunks_indexed=0, ok=False, error=str(exc))
        return IndexResult(doc_id=doc_id, chunks_indexed=len(chunks))

    def documents(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, title, source_path, source_type, pages, created_at FROM documents ORDER BY created_at"
            ).fetchall()
        return [
            {
                "doc_id": r[0],
                "title": r[1],
                "source_path": r[2],
                "source_type": r[3],
                "pages": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    # ── retrieval ──────────────────────────────────────────────────────────
    def query(self, question: str, top_k: int = 3) -> QueryResult:
        """Return the best-matching chunk(s) above threshold. If nothing is over
        the bar we answer 'not found' — never a guess."""
        if self.embedder is None:
            return QueryResult(answer="", found=False, below_threshold=True)
        q = self.embedder.embed(question)
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.chunk_id, c.doc_id, c.seq, c.text, c.page, c.start_char, c.end_char, "
                "c.embedding, d.title, d.source_path "
                "FROM chunks c JOIN documents d ON d.doc_id = c.doc_id"
            ).fetchall()
        scored: list[tuple[float, tuple]] = []
        for row in rows:
            vec = json.loads(row[7])
            score = cosine_similarity(q, vec)
            if score >= self.threshold:
                scored.append((score, row))
        scored.sort(key=lambda t: t[0], reverse=True)

        if not scored:
            return QueryResult(
                answer="Not found in your indexed material.",
                found=False,
                below_threshold=True,
                detail={"threshold": self.threshold, "chunks_checked": len(rows)},
            )

        top = scored[:top_k]
        chunks = [
            RetrievedChunk(
                text=r[3],
                score=score,
                doc_id=r[1],
                title=r[8],
                source_path=r[9],
                page=r[4],
                seq=r[2],
                start_char=r[5],
                end_char=r[6],
            )
            for score, r in top
        ]
        best = chunks[0]
        citation = f"{best.title} (page {best.page})" if best.page else best.title
        answer = f"According to {citation}: {best.text[:500]}"
        return QueryResult(
            answer=answer,
            found=True,
            citation=citation,
            chunks=chunks,
            below_threshold=False,
            detail={"threshold": self.threshold, "chunks_checked": len(rows), "top_score": best.score},
        )