"""Opt-in state for routine suggestions (Phase 7.4).

Mining stays dark until the user explicitly opts in. Accepting a suggestion
records an automation proposal in this local JSON file — nothing is scheduled
or executed by this module, and an acceptance can only be written through the
Execute-tier `system.create_routine` tool after its diff card was approved.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def default_routine_settings_path() -> Path:
    return Path(os.getenv("VIORIS_DATA_DIR", "vioris_data")) / "routine_settings.json"


class RoutineSettingsStore:
    """Small durable opt-in ledger; fails closed when unreadable/corrupt."""

    DEFAULTS: dict[str, Any] = {"opted_in": False, "accepted": {}}

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or default_routine_settings_path())
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(dict(self.DEFAULTS))

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return dict(self.DEFAULTS)
            return {
                "opted_in": bool(data.get("opted_in", False)),
                "accepted": data.get("accepted") if isinstance(data.get("accepted"), dict) else {},
            }
        except (OSError, ValueError):
            # Corrupt or unreadable settings never enable mining or automation.
            return dict(self.DEFAULTS)

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def opted_in(self) -> bool:
        with self._lock:
            return self._read()["opted_in"]

    def set_opted_in(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            data["opted_in"] = bool(enabled)
            self._write(data)
        return {"opted_in": bool(enabled)}

    def accepted(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._read()["accepted"])

    def is_accepted(self, pattern_id: str) -> bool:
        return pattern_id in self.accepted()

    def accept(self, pattern_id: str, detail: dict[str, Any]) -> dict[str, Any]:
        """Record an approved automation proposal; no scheduling occurs here."""
        with self._lock:
            data = self._read()
            record = {
                "pattern_id": pattern_id,
                "accepted_at": datetime.now(UTC).isoformat(),
                **detail,
            }
            data["accepted"][pattern_id] = record
            self._write(data)
        return record
