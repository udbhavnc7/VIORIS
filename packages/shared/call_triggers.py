"""
Proactive Call Triggers (Phase 8.5, item 10e).

The digest scheduler already fires at fixed hours. This module adds the
user's own triggers:

  - "check in every evening at 7pm"           -> kind="time", at="19:00"
  - "call me when the deploy task finishes"   -> kind="task_completed",
                                                  match="deploy"

Triggers are local, explicit, and observable. Nothing here executes a task or
sends a message; a trigger only decides that Vioris should *call* the user.
The call itself still composes its digest from the connectors and still asks
before acting.

Persistence is a single JSON file under VIORIS_DATA_DIR. Writes are atomic
(temp file + replace) so a crash cannot leave a half-written trigger list.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable

#: A time trigger may fire this long after its scheduled minute. The background
#: loop wakes every 60s; a short grace window prevents a missed minute from
#: silently skipping the call without letting a stale 7am trigger fire at noon.
DEFAULT_GRACE_MINUTES = 10

#: Keep the per-trigger fired-task history bounded. The store only needs enough
#: ids to avoid duplicate calls for recently completed tasks.
MAX_FIRED_TASK_IDS = 200


class TriggerValidationError(ValueError):
    """A trigger is malformed or asks for something the store cannot honour."""


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return parsed if parsed.tzinfo else parsed.astimezone()
    return None


@dataclass
class CallTrigger:
    """One user-configured reason for Vioris to place a proactive call."""

    trigger_id: str
    kind: str  # "time" | "task_completed"
    label: str = ""
    at: str = ""  # "HH:MM" for kind="time"
    match: str = ""  # case-insensitive substring for kind="task_completed"
    enabled: bool = True
    one_shot: bool = False
    created_at: str = field(default_factory=lambda: _now().isoformat(timespec="seconds"))
    last_fired_at: str | None = None
    fired_task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "kind": self.kind,
            "label": self.label,
            "at": self.at,
            "match": self.match,
            "enabled": self.enabled,
            "one_shot": self.one_shot,
            "created_at": self.created_at,
            "last_fired_at": self.last_fired_at,
            "fired_task_ids": list(self.fired_task_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CallTrigger":
        return cls(
            trigger_id=str(data.get("trigger_id") or uuid.uuid4().hex[:12]),
            kind=str(data.get("kind") or ""),
            label=str(data.get("label") or ""),
            at=str(data.get("at") or ""),
            match=str(data.get("match") or ""),
            enabled=bool(data.get("enabled", True)),
            one_shot=bool(data.get("one_shot", False)),
            created_at=str(data.get("created_at") or _now().isoformat(timespec="seconds")),
            last_fired_at=data.get("last_fired_at"),
            fired_task_ids=[str(x) for x in (data.get("fired_task_ids") or [])],
        )

    @property
    def description(self) -> str:
        if self.label:
            return self.label
        if self.kind == "time":
            return f"Call every day at {self.at}"
        if self.kind == "task_completed":
            return f"Call when a task matching '{self.match}' completes"
        return f"Call trigger {self.trigger_id}"


@dataclass
class TriggerMatch:
    """A trigger that is due, plus the context that made it due."""

    trigger: CallTrigger
    reason: str
    task_id: str = ""
    task_request: str = ""


class CallTriggerStore:
    """JSON-backed store for user-defined call triggers."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        grace_minutes: int = DEFAULT_GRACE_MINUTES,
    ) -> None:
        if path is None:
            data_dir = Path(os.getenv("VIORIS_DATA_DIR", "vioris_data"))
            path = data_dir / "call_triggers.json"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.grace_minutes = grace_minutes
        self._lock = threading.Lock()
        self._triggers: list[CallTrigger] = self._load()

    # ── persistence ───────────────────────────────────────────────────────

    def _load(self) -> list[CallTrigger]:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        triggers: list[CallTrigger] = []
        for item in raw:
            if isinstance(item, dict):
                try:
                    triggers.append(CallTrigger.from_dict(item))
                except Exception:  # noqa: BLE001 - skip one corrupt row, keep the rest
                    continue
        return triggers

    def _save(self) -> None:
        payload = [trigger.to_dict() for trigger in self._triggers]
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), "utf-8")
        tmp.replace(self.path)

    # ── CRUD ──────────────────────────────────────────────────────────────

    def list(self) -> list[CallTrigger]:
        with self._lock:
            return list(self._triggers)

    def get(self, trigger_id: str) -> CallTrigger | None:
        with self._lock:
            return next((t for t in self._triggers if t.trigger_id == trigger_id), None)

    def create(
        self,
        *,
        kind: str,
        label: str = "",
        at: str | None = None,
        match: str | None = None,
        one_shot: bool = False,
    ) -> CallTrigger:
        kind = (kind or "").strip()
        if kind not in ("time", "task_completed"):
            raise TriggerValidationError("kind must be 'time' or 'task_completed'")

        if kind == "time":
            if not at:
                raise TriggerValidationError("a time trigger needs 'at' in HH:MM form")
            try:
                parsed = time.fromisoformat(at)
            except ValueError as exc:
                raise TriggerValidationError("'at' must be a 24-hour HH:MM time") from exc
            at = parsed.strftime("%H:%M")
            match = ""
        else:
            match = (match or "").strip()
            if not match:
                raise TriggerValidationError(
                    "a task_completed trigger needs a non-empty 'match' substring"
                )
            at = ""

        trigger = CallTrigger(
            trigger_id=uuid.uuid4().hex[:12],
            kind=kind,
            label=(label or "").strip(),
            at=at or "",
            match=match or "",
            one_shot=bool(one_shot),
        )
        with self._lock:
            self._triggers.append(trigger)
            self._save()
        return trigger

    def delete(self, trigger_id: str) -> bool:
        with self._lock:
            before = len(self._triggers)
            self._triggers = [t for t in self._triggers if t.trigger_id != trigger_id]
            if len(self._triggers) == before:
                return False
            self._save()
            return True

    def set_enabled(self, trigger_id: str, enabled: bool) -> CallTrigger | None:
        with self._lock:
            for trigger in self._triggers:
                if trigger.trigger_id == trigger_id:
                    trigger.enabled = bool(enabled)
                    self._save()
                    return trigger
        return None

    # ── evaluation ────────────────────────────────────────────────────────

    def due(
        self,
        *,
        now: datetime | None = None,
        completed_tasks: Iterable[dict[str, Any]] | None = None,
    ) -> list[TriggerMatch]:
        """Return triggers that should place a call right now.

        This is a pure read: callers mark a match fired only after the call is
        actually created, so a failed call can be retried on the next tick.
        """
        now = now or _now()
        tasks = list(completed_tasks or [])
        matches: list[TriggerMatch] = []

        with self._lock:
            snapshot = [CallTrigger.from_dict(t.to_dict()) for t in self._triggers]

        for trigger in snapshot:
            if not trigger.enabled:
                continue
            if trigger.kind == "time":
                if self._time_due(trigger, now):
                    matches.append(TriggerMatch(trigger=trigger, reason=trigger.description))
            elif trigger.kind == "task_completed":
                for task in tasks:
                    if not self._task_matches(trigger, task):
                        continue
                    task_id = str(task.get("task_id") or "")
                    request = str(task.get("request") or "")
                    matches.append(
                        TriggerMatch(
                            trigger=trigger,
                            reason=f"Task completed: {request}" if request else "Task completed",
                            task_id=task_id,
                            task_request=request,
                        )
                    )
                    break
        return matches

    def _time_due(self, trigger: CallTrigger, now: datetime) -> bool:
        try:
            target = time.fromisoformat(trigger.at)
        except (ValueError, TypeError):
            return False
        scheduled = now.replace(
            hour=target.hour, minute=target.minute, second=0, microsecond=0
        )
        delta = (now - scheduled).total_seconds()
        if delta < 0 or delta > self.grace_minutes * 60:
            return False
        last = _parse_dt(trigger.last_fired_at)
        if last is not None and last.date() == now.date():
            return False
        return True

    @staticmethod
    def _task_matches(trigger: CallTrigger, task: dict[str, Any]) -> bool:
        status = str(task.get("status") or "").lower()
        if status not in ("completed", "succeeded", "success"):
            return False
        task_id = str(task.get("task_id") or "")
        if not task_id or task_id in trigger.fired_task_ids:
            return False
        needle = trigger.match.lower().strip()
        if needle and needle not in str(task.get("request") or "").lower():
            return False

        created = _parse_dt(trigger.created_at)
        updated = _parse_dt(task.get("updated_at") or task.get("completed_at"))
        if created is not None and updated is not None and updated < created:
            # A historical completion is not "when a phase finishes"; it already
            # finished before the user asked to be told.
            return False
        return True

    def mark_fired(
        self,
        trigger_id: str,
        *,
        now: datetime | None = None,
        task_id: str = "",
    ) -> CallTrigger | None:
        """Record that a call was placed for this trigger."""
        now = now or _now()
        with self._lock:
            for trigger in self._triggers:
                if trigger.trigger_id != trigger_id:
                    continue
                trigger.last_fired_at = now.isoformat(timespec="seconds")
                if task_id:
                    if task_id not in trigger.fired_task_ids:
                        trigger.fired_task_ids.append(task_id)
                    trigger.fired_task_ids = trigger.fired_task_ids[-MAX_FIRED_TASK_IDS:]
                if trigger.one_shot:
                    trigger.enabled = False
                self._save()
                return trigger
        return None
