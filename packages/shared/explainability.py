"""
Explainability module (Phase 8, Prompt 8.4).

Full explainability, queryable by voice. Extends the audit event schema to
capture the reasoning and alternatives considered for each plan step, and
adds a query tool that lets the user ask "why did you do that?" against the
audit log via RAG.

This turns a black-box agent into one you can audit by asking it, out loud.

How it works:
  1. When the planner creates a plan, each step includes a `reasoning` field
     explaining why that tool was chosen and what alternatives were rejected.
  2. These reasoning records are stored alongside audit events.
  3. When the user asks "why did you do X?", the system searches the audit log
     for the relevant event, retrieves the reasoning, and returns a natural
     language explanation.

The reasoning is stored as structured data (not free text) so it can be
searched, filtered, and presented consistently.
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
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StepReasoning:
    """The reasoning behind a single plan step.

    Stored alongside the audit event so the user can always ask "why did
    you do X?" and get a structured answer.
    """

    reasoning_id: str = field(default_factory=lambda: f"reason_{uuid.uuid4().hex[:8]}")
    task_id: str = ""
    step_id: str = ""
    tool: str = ""
    # Why this tool was chosen
    chosen_reason: str = ""
    # What alternatives were considered and why they were rejected
    alternatives: list[dict[str, str]] = field(default_factory=list)
    # Risk classification rationale
    risk_rationale: str = ""
    # User request that triggered this step
    user_request: str = ""
    # Timestamp
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


_REASONING_SCHEMA = """
CREATE TABLE IF NOT EXISTS step_reasoning (
    reasoning_id   TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    step_id        TEXT NOT NULL,
    tool           TEXT NOT NULL,
    chosen_reason  TEXT NOT NULL,
    alternatives   TEXT NOT NULL DEFAULT '[]',
    risk_rationale TEXT NOT NULL DEFAULT '',
    user_request   TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reasoning_task ON step_reasoning(task_id);
CREATE INDEX IF NOT EXISTS idx_reasoning_step ON step_reasoning(step_id);
CREATE INDEX IF NOT EXISTS idx_reasoning_tool ON step_reasoning(tool);
"""


class ReasoningStore:
    """SQLite-backed store for step reasoning. Paired with the audit log."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_REASONING_SCHEMA)
        self._conn.commit()

    def store(self, reasoning: StepReasoning) -> str:
        """Store reasoning for a plan step. Returns the reasoning_id."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO step_reasoning "
                "(reasoning_id, task_id, step_id, tool, chosen_reason, alternatives, risk_rationale, user_request) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reasoning.reasoning_id,
                    reasoning.task_id,
                    reasoning.step_id,
                    reasoning.tool,
                    reasoning.chosen_reason,
                    json.dumps(reasoning.alternatives),
                    reasoning.risk_rationale,
                    reasoning.user_request,
                ),
            )
            self._conn.commit()
        return reasoning.reasoning_id

    def get_by_step(self, task_id: str, step_id: str) -> StepReasoning | None:
        """Get reasoning for a specific step."""
        with self._lock:
            row = self._conn.execute(
                "SELECT reasoning_id, task_id, step_id, tool, chosen_reason, "
                "alternatives, risk_rationale, user_request, created_at "
                "FROM step_reasoning WHERE task_id=? AND step_id=?",
                (task_id, step_id),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_reasoning(row)

    def get_by_task(self, task_id: str) -> list[StepReasoning]:
        """Get all reasoning for a task, in order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT reasoning_id, task_id, step_id, tool, chosen_reason, "
                "alternatives, risk_rationale, user_request, created_at "
                "FROM step_reasoning WHERE task_id=? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_reasoning(r) for r in rows]

    def search_by_tool(self, tool: str, limit: int = 20) -> list[StepReasoning]:
        """Search reasoning by tool name."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT reasoning_id, task_id, step_id, tool, chosen_reason, "
                "alternatives, risk_rationale, user_request, created_at "
                "FROM step_reasoning WHERE tool=? ORDER BY created_at DESC LIMIT ?",
                (tool, limit),
            ).fetchall()
        return [self._row_to_reasoning(r) for r in rows]

    def search_by_request(self, query: str, limit: int = 10) -> list[StepReasoning]:
        """Simple text search over user requests and chosen reasons."""
        pattern = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT reasoning_id, task_id, step_id, tool, chosen_reason, "
                "alternatives, risk_rationale, user_request, created_at "
                "FROM step_reasoning "
                "WHERE user_request LIKE ? OR chosen_reason LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (pattern, pattern, limit),
            ).fetchall()
        return [self._row_to_reasoning(r) for r in rows]

    def recent(self, limit: int = 20) -> list[StepReasoning]:
        """Get recent reasoning records."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT reasoning_id, task_id, step_id, tool, chosen_reason, "
                "alternatives, risk_rationale, user_request, created_at "
                "FROM step_reasoning ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_reasoning(r) for r in rows]

    def _row_to_reasoning(self, row: tuple) -> StepReasoning:
        return StepReasoning(
            reasoning_id=row[0],
            task_id=row[1],
            step_id=row[2],
            tool=row[3],
            chosen_reason=row[4],
            alternatives=json.loads(row[5]),
            risk_rationale=row[6],
            user_request=row[7],
            created_at=row[8],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class ExplainabilityEngine:
    """High-level explainability interface.

    Combines the reasoning store with a natural-language response generator
    so the user can ask "why did you do that?" and get a spoken answer.
    """

    def __init__(self, reasoning_store: ReasoningStore) -> None:
        self.store = reasoning_store

    def explain_step(self, task_id: str, step_id: str) -> str:
        """Generate a natural language explanation for a specific step."""
        reasoning = self.store.get_by_step(task_id, step_id)
        if reasoning is None:
            return f"No reasoning found for step {step_id} in task {task_id}."

        return self._format_explanation(reasoning)

    def explain_task(self, task_id: str) -> str:
        """Generate a natural language explanation for an entire task."""
        reasoning_list = self.store.get_by_task(task_id)
        if not reasoning_list:
            return f"No reasoning found for task {task_id}."

        parts = [f"Here's why I did what I did for that task:"]
        for i, r in enumerate(reasoning_list, 1):
            parts.append(f"\nStep {i} ({r.tool}): {r.chosen_reason}")
            if r.alternatives:
                rejected = [a.get("tool", "?") for a in r.alternatives if a.get("rejected")]
                if rejected:
                    parts.append(f"  I considered {', '.join(rejected)} but chose {r.tool} because {r.chosen_reason}")
        return "\n".join(parts)

    def explain_recent(self, query: str | None = None, limit: int = 5) -> str:
        """Explain recent actions, optionally filtered by a search query."""
        if query:
            reasoning_list = self.store.search_by_request(query, limit=limit)
            if not reasoning_list:
                return f"I couldn't find any recent actions matching '{query}'."
        else:
            reasoning_list = self.store.recent(limit=limit)

        if not reasoning_list:
            return "I haven't taken any actions yet."

        parts = ["Here are the most recent things I did:"]
        for r in reasoning_list:
            parts.append(f"\n- {r.tool}: {r.chosen_reason}")
        return "\n".join(parts)

    def why_tool(self, tool_name: str, limit: int = 5) -> str:
        """Explain why a specific tool was used."""
        reasoning_list = self.store.search_by_tool(tool_name, limit=limit)
        if not reasoning_list:
            return f"I haven't used {tool_name} recently."

        parts = [f"Here's why I used {tool_name}:"]
        for r in reasoning_list:
            parts.append(f"\n- {r.chosen_reason}")
            if r.alternatives:
                rejected = [a.get("tool", "?") for a in r.alternatives if a.get("rejected")]
                if rejected:
                    parts.append(f"  Instead of: {', '.join(rejected)}")
        return "\n".join(parts)

    def _format_explanation(self, reasoning: StepReasoning) -> str:
        """Format a single reasoning record into natural language."""
        parts = [f"I used {reasoning.tool} because {reasoning.chosen_reason}."]

        if reasoning.alternatives:
            rejected = [a for a in reasoning.alternatives if a.get("rejected")]
            if rejected:
                alt_names = [a.get("tool", "something") for a in rejected]
                parts.append(f"I considered {', '.join(alt_names)} but chose not to because:")
                for a in rejected:
                    parts.append(f"  - {a.get('reason', 'it was less suitable')}")

        if reasoning.risk_rationale:
            parts.append(f"Risk assessment: {reasoning.risk_rationale}")

        return "\n".join(parts)


def record_planning_reasoning(
    reasoning_store: ReasoningStore,
    task_id: str,
    step_id: str,
    tool: str,
    chosen_reason: str,
    alternatives: list[dict[str, str]] | None = None,
    risk_rationale: str = "",
    user_request: str = "",
) -> str:
    """Convenience function to record reasoning for a plan step.

    Called by the planner when it creates a plan. Returns the reasoning_id.
    """
    reasoning = StepReasoning(
        task_id=task_id,
        step_id=step_id,
        tool=tool,
        chosen_reason=chosen_reason,
        alternatives=alternatives or [],
        risk_rationale=risk_rationale,
        user_request=user_request,
    )
    return reasoning_store.store(reasoning)
