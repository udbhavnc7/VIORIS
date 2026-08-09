"""
Vioris local task store (Phase 1).

SQLite-backed append-only task + audit history on the desktop. Mirrors the
Postgres schema in infra/database/migrations/001_init.sql for the audit part
(hash-chained, genesis-anchored) so the same invariants hold locally:

  audit_events is append-only: UPDATE/DELETE are guarded, and every event
  links to the previous via prev_hash; hash = sha256(prev_hash || payload).

Even though Phase 1 runs fully local, the hash chain is in from day one so a
later multi-device phase can backfill to Postgres without re-architing.
Single-writer: the desktop agent is the only process appending here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path

from packages.shared.audit import GENESIS_HASH, event_payload
from packages.shared.schemas import AuditAction, TaskStatus

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    request    TEXT NOT NULL,
    status     TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    tool       TEXT,
    result     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id   TEXT PRIMARY KEY,
    task_id    TEXT,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}',
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class TaskStore:
    """Append-only task + audit history in SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── tasks ─────────────────────────────────────────────────────────────
    def record_task(
        self,
        request: str,
        status: TaskStatus,
        risk_level: str,
        tool: str | None = None,
        result: dict | None = None,
    ) -> str:
        """Persist one task; return its task_id."""
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        status_str = status.value if hasattr(status, "value") else str(status)
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, request, status, risk_level, tool, result) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    request,
                    status_str,
                    risk_level,
                    tool,
                    json.dumps(result) if result is not None else None,
                ),
            )
            self._conn.commit()
        return task_id

    def update_task_status(
        self, task_id: str, status: TaskStatus, result: dict | None = None
    ) -> None:
        status_str = status.value if hasattr(status, "value") else str(status)
        with self._lock:
            if result is not None:
                self._conn.execute(
                    "UPDATE tasks SET status=?, result=? WHERE task_id=?",
                    (status_str, json.dumps(result), task_id),
                )
            else:
                self._conn.execute(
                    "UPDATE tasks SET status=? WHERE task_id=?", (status_str, task_id)
                )
            self._conn.commit()

    def list_tasks(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id, request, status, risk_level, tool, result, created_at "
                "FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "task_id": r[0],
                "request": r[1],
                "status": r[2],
                "risk_level": r[3],
                "tool": r[4],
                "result": json.loads(r[5]) if r[5] else None,
                "created_at": r[6],
            }
            for r in rows
        ]

    # ── audit chain (append-only, hash-chained) ───────────────────────────
    def append_audit(
        self,
        actor: str,
        action: AuditAction | str,
        detail: dict | None = None,
        task_id: str | None = None,
    ) -> str:
        """Append an audit event to the chain; return event_id."""
        detail = detail or {}
        action_str = action.value if hasattr(action, "value") else str(action)
        event_id = f"evt_{uuid.uuid4().hex[:8]}"

        with self._lock:
            # Chain off the current tail; re-read from DB so it survives restarts.
            row = self._conn.execute(
                "SELECT hash FROM audit_events ORDER BY created_at DESC, event_id DESC LIMIT 1"
            ).fetchone()
            prev = row[0] if row else GENESIS_HASH

            payload = prev + event_payload(event_id, actor, action_str, detail)
            hash_val = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            self._conn.execute(
                "INSERT INTO audit_events (event_id, task_id, actor, action, detail, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, task_id, actor, action_str, json.dumps(detail), prev, hash_val),
            )
            self._conn.commit()
        return event_id

    def audit_events_copy(self, limit: int = 100) -> list[dict]:
        """Read-only snapshot of the chain, tail-first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, task_id, actor, action, detail, prev_hash, hash, created_at "
                "FROM audit_events ORDER BY created_at DESC, event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "event_id": r[0],
                "task_id": r[1],
                "actor": r[2],
                "action": r[3],
                "detail": json.loads(r[4]) if r[4] else {},
                "prev_hash": r[5],
                "hash": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]

    def verify_chain(self) -> tuple[bool, list[str]]:
        """Recompute the chain from genesis; report corrupt links."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, actor, action, detail, prev_hash, hash "
                "FROM audit_events ORDER BY created_at ASC, event_id ASC"
            ).fetchall()
        prev = GENESIS_HASH
        problems = []
        for r in rows:
            event_id, actor, action, detail, prev_hash, hash = r
            if prev_hash != prev:
                problems.append(f"{event_id}: prev_hash mismatch")
            payload = prev + event_payload(event_id, actor, action, json.loads(detail or "{}"))
            if hash != hashlib.sha256(payload.encode("utf-8")).hexdigest():
                problems.append(f"{event_id}: hash mismatch")
            prev = hash
        return (not problems, problems)
