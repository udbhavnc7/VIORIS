"""
Scripted mock History transport for tests (Phase 6).

Implements the HistoryTransport protocol with canned recent-visit rows and
injectable failures, so connector tests never touch a real browser profile.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from integrations.base import ConnectorError

_CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _chromium_ts(hours_ago: float) -> int:
    return int(
        (datetime.now(timezone.utc) - timedelta(hours=hours_ago) - _CHROMIUM_EPOCH).total_seconds() * 1_000_000
    )


_DEFAULT_ROWS = [
    {"url": "https://vioris.example/docs", "title": "Vioris Docs", "last_visit_time": _chromium_ts(0.5)},
    {"url": "https://news.example/ai", "title": "AI News", "last_visit_time": _chromium_ts(2)},
    {"url": "https://old.example/archived", "title": "Archived", "last_visit_time": _chromium_ts(200)},
]


class MockHistoryTransport:
    """A Chromium-history-shaped provider with canned data + failure injection."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.name = "history"
        self.rows = rows if rows is not None else _DEFAULT_ROWS
        self.calls: list[str] = []
        self.fail_missing: bool = False
        self.fail_corrupt: bool = False

    def read_recent(self, source: str, hours: int) -> list[dict]:
        self.calls.append(f"read_recent:{source}:{hours}")
        if self.fail_missing:
            raise ConnectorError(f"history file not found: {source}")
        if self.fail_corrupt:
            raise ConnectorError("unreadable history file: not a database")
        cutoff = _chromium_ts(hours)
        return [r for r in self.rows if r["last_visit_time"] >= cutoff]
