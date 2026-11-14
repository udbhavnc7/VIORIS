"""
Approval flow (Phase 2, Prompt 2.3).

When a step is tagged execute/critical, the engine pauses it and creates an
ApprovalRequest with a full diff card. The step runs ONLY after an explicit
`approve` tied to that step's idempotency_key — a stale or cross-step key is
rejected. Reject abandons the step (it never fires). The approval ledger is
Append-only and checked the way the rest of the audit chain is.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.shared.permission_engine import PermissionEngine

logger = logging.getLogger(__name__)

def action_hash(tool: str, args: dict) -> str:
    """Canonical fingerprint of the exact action a user approves.

    IDs alone are not enough: if the step's recipient/content changes after
    approval, the approval must no longer be valid. Hashing the tool plus the
    full argument set binds consent to the action, not to a row number.
    """
    canonical = json.dumps(
        {"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_APPROVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id      TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    tool             TEXT NOT NULL,
    diff_card        TEXT NOT NULL,
    status           TEXT NOT NULL,           -- pending | approved | rejected
    action_hash      TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at       TEXT
);
"""


@dataclass
class ApprovalRequest:
    approval_id: str
    task_id: str
    step_id: str
    idempotency_key: str
    tool: str
    diff_card: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    action_hash: str = ""
    created_at: str = ""
    decided_at: str | None = None


class ApprovalStore:
    """SQLite-backed approval ledger, keyed by step idempotency_key."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False: see TaskManager — all access is serialized
        # through _lock, so the connection may cross threads.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_APPROVAL_SCHEMA)
        # migrate pre-existing ledgers that predate action binding
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(approvals)")}
        if "action_hash" not in cols:
            self._conn.execute(
                "ALTER TABLE approvals ADD COLUMN action_hash TEXT NOT NULL DEFAULT ''"
            )
        self._conn.commit()

    def create(self, task_id: str, step_id: str, idempotency_key: str, tool: str, args: dict) -> ApprovalRequest:
        approval_id = f"apr_{uuid.uuid4().hex[:10]}"
        diff = _diff_card(tool, args)
        digest = action_hash(tool, args)
        created_at = datetime.now(UTC).isoformat()
        req = ApprovalRequest(
            approval_id=approval_id,
            task_id=task_id,
            step_id=step_id,
            idempotency_key=idempotency_key,
            tool=tool,
            diff_card=diff,
            action_hash=digest,
            created_at=created_at,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO approvals (approval_id, task_id, step_id, idempotency_key, tool, diff_card, status, action_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (approval_id, task_id, step_id, idempotency_key, tool, json.dumps(diff), digest, created_at),
            )
            self._conn.commit()
        return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT approval_id, task_id, step_id, idempotency_key, tool, diff_card, status, action_hash, created_at, decided_at "
                "FROM approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_request(row)

    def _set_status(self, approval_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE approvals SET status=?, decided_at=datetime('now') WHERE approval_id=?",
                (status, approval_id),
            )
            self._conn.commit()

    def approve(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Mark approved. The caller then arms/run the step keyed to this
        approval's idempotency_key — the approval is not generic."""
        if approval.status != "pending":
            raise ValueError(f"approval {approval.approval_id} already {approval.status}")
        self._set_status(approval.approval_id, "approved")
        approval.status = "approved"
        return approval

    def reject(self, approval: ApprovalRequest) -> ApprovalRequest:
        if approval.status != "pending":
            raise ValueError(f"approval {approval.approval_id} already {approval.status}")
        self._set_status(approval.approval_id, "rejected")
        approval.status = "rejected"
        return approval

    def list_pending(self, task_id: str | None = None) -> list[ApprovalRequest]:
        with self._lock:
            if task_id:
                rows = self._conn.execute(
                    "SELECT approval_id, task_id, step_id, idempotency_key, tool, diff_card, status, action_hash, created_at, decided_at "
                    "FROM approvals WHERE task_id=? AND status='pending'",
                    (task_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT approval_id, task_id, step_id, idempotency_key, tool, diff_card, status, action_hash, created_at, decided_at "
                    "FROM approvals WHERE status='pending'"
                ).fetchall()
        return [self._row_to_request(r) for r in rows]

    @staticmethod
    def _row_to_request(row) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=row[0],
            task_id=row[1],
            step_id=row[2],
            idempotency_key=row[3],
            tool=row[4],
            diff_card=json.loads(row[5]),
            status=row[6],
            action_hash=row[7] if len(row) > 7 and row[7] else "",
            created_at=(row[8] or "") if len(row) > 8 else "",
            decided_at=(row[9] or None) if len(row) > 9 else None,
        )

    def is_approved_for_step(
        self,
        task_id: str,
        step_id: str,
        idempotency_key: str,
        tool: str | None = None,
        args: dict | None = None,
    ) -> bool:
        """True only if an explicit 'approved' record exists for THIS step,
        key, and — when provided — the exact action contents."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status, action_hash FROM approvals "
                "WHERE task_id=? AND step_id=? AND idempotency_key=?",
                (task_id, step_id, idempotency_key),
            ).fetchone()
        if row is None or row[0] != "approved":
            return False
        if tool is not None and args is not None:
            if row[1] != action_hash(tool, args):
                return False
        return True

    def invalidate_for_step(self, task_id: str, step_id: str) -> int:
        """Invalidate all approvals for a step (set to rejected).

        Called when retrying an Execute/Critical step — the old approval
        must not be reusable. Invalidates both pending and approved approvals.
        Returns the number of approvals invalidated.
        """
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE approvals SET status='rejected', decided_at=datetime('now') "
                "WHERE task_id=? AND step_id=? AND status IN ('pending', 'approved')",
                (task_id, step_id),
            )
            self._conn.commit()
            return cursor.rowcount

    def tail_link(self) -> str:
        """Stable placeholder hash so the ledger doesn't need a genesis column."""
        return hashlib.sha256(b"approval-ledger").hexdigest()


def _diff_card(tool: str, args: dict) -> dict:
    """Render the exact "what will happen" card. Restricts to the fields the
    tool registered — the LLM's extra keys are dropped so the card can't hide
    receiver/amount behind unrelated noise."""
    fields = _diff_card_fields(tool)
    return {fld: args.get(fld) for fld in fields if fld in args}


def _diff_card_fields(tool: str) -> list[str]:
    reg = next((f for f in PermissionEngine.list_tools() if f.tool_name == tool), None)
    return list(reg.diff_card_fields) if reg else []