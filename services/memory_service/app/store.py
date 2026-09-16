"""
Memory Store (Phase 4, Prompt 4.1).

Persists memories separated by category into five buckets (preferences, people,
projects, routines, sensitive). Handles opt-in for sensitive writes, source
tracking, and confidence. Backed by SQLite locally; the schema mirrors the
pgvector table shape from docs/02-architecture.md so the same columns (plus an
embedding column added in Prompt 4.2) can be lifted to Postgres later.

Invariants:
  - A category label that isn't one of the five is rejected at the boundary.
  - Any write (`save` / `correct`) in the `sensitive` category requires
    `user_opted_in=True`; otherwise it fails with OptInRequiredError and
    nothing is persisted.
  - Every record stores `source`, `created_at`, and `confidence`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from packages.shared.schemas import Memory, MemoryCategory

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id      TEXT PRIMARY KEY,
    category       TEXT NOT NULL,
    content        TEXT NOT NULL,
    source         TEXT NOT NULL,
    confidence     REAL NOT NULL DEFAULT 1.0,
    user_opted_in  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    embedding      BLOB,
    CHECK (category IN ('preferences','people','projects','routines','sensitive'))
);

CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
"""


class SensitiveOptInRequiredError(Exception):
    """The write targeted the sensitive category without user_opted_in=True."""


@dataclass
class StoreResult:
    memory: Memory | None = None
    ok: bool = True
    error: str | None = None
    detail: dict = field(default_factory=dict)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_memory(row: tuple) -> Memory:
    return Memory(
        memory_id=row[0],
        category=MemoryCategory(row[1]),
        content=row[2],
        source=row[3],
        confidence=row[4],
        user_opted_in=bool(row[5]),
        created_at=datetime.fromisoformat(row[6]),
        updated_at=datetime.fromisoformat(row[7]),
    )


class MemoryStore:
    """SQLite-backed memory ledger, keyed by category + memory_id."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass

    def add(self, category: MemoryCategory, content: str, source: str, confidence: float = 1.0) -> StoreResult:
        return self.store_and_user_opt_in(category, content, source, confidence, user_opted_in=False)

    def store_and_user_opt_in(
        self,
        category: MemoryCategory,
        content: str,
        source: str,
        confidence: float = 1.0,
        user_opted_in: bool = False,
    ) -> StoreResult:
        if category == MemoryCategory.SENSITIVE and not user_opted_in:
            logger.warning("Sensitive write refused without explicit opt-in")
            return StoreResult(
                ok=False,
                error="sensitive category requires user_opted_in=true before any write succeeds",
            )
        memory_id = f"mem_{uuid.uuid4().hex[:8]}"
        now = _now()
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO memories (memory_id, category, content, source,
                                          confidence, user_opted_in, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        category.value,
                        content,
                        source,
                        float(confidence),
                        int(user_opted_in),
                        now,
                        now,
                    ),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                logger.error("Failed to store memory: %s", exc)
                return StoreResult(ok=False, error=str(exc))
        return StoreResult(memory=self.get(memory_id).memory)

    def get(self, memory_id: str) -> StoreResult:
        with self._lock:
            row = self._conn.execute(
                "SELECT memory_id, category, content, source, confidence, user_opted_in, "
                "created_at, updated_at FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return StoreResult(ok=False, error=f"memory '{memory_id}' not found")
        return StoreResult(memory=_row_to_memory(row))

    def list(self, category: MemoryCategory | None = None) -> list[Memory]:
        with self._lock:
            if category is None:
                rows = self._conn.execute(
                    "SELECT memory_id, category, content, source, confidence, user_opted_in, "
                    "created_at, updated_at FROM memories ORDER BY created_at"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT memory_id, category, content, source, confidence, user_opted_in, "
                    "created_at, updated_at FROM memories WHERE category = ? ORDER BY created_at",
                    (category.value,),
                ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def correct(self, memory_id: str, content: str, *, category_overwrite: MemoryCategory | None = None) -> StoreResult:
        """Correct a memory's content (and optionally re-categorize it).

        `category_overwrite` may change a record to/from `sensitive`. Any result
        that ends up in the `sensitive` category REQUIRES user_opted_in=True —
        we only copy the flag from the existing row when it was already True.
        """
        existing = self.get(memory_id)
        if not existing.ok:
            return existing
        mem = existing.memory
        target_cat = category_overwrite or mem.category
        kept_opt_in = mem.user_opted_in
        if target_cat == MemoryCategory.SENSITIVE and not kept_opt_in:
            return StoreResult(ok=False, error="cannot correct into sensitive without user_opted_in=True")
        now = _now()
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE memories SET content = ?, category = ?, "
                    "user_opted_in = ?, updated_at = ? WHERE memory_id = ?",
                    (content, target_cat.value, int(kept_opt_in), now, memory_id),
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                return StoreResult(ok=False, error=str(exc))
        return self.get(memory_id)

    def delete(self, memory_id: str) -> StoreResult:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
            self._conn.commit()
            deleted = cur.rowcount > 0
        return StoreResult(ok=deleted, detail={"memory_id": memory_id, "deleted": deleted})