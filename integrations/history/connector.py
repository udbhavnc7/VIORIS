"""
Browser history connector — READ-ONLY (Phase 6, order: … → Browser
bookmarks/history → …).

Uses the local browser's history store (the Chromium-family `History` SQLite
file on the laptop), NOT a remote account — no OAuth, no network token. The
connector opens the profile DB read-only (`mode=ro` URI), reads recent visits,
and produces a searchable digest. Write scopes are empty: no code ever writes
to the profile.

Because there is no OAuth, connect/refresh/revoke mirror the base template but
operate on the local file: connect points the connector at a history file,
disconnect forgets it. Rate-limit / expired-session handling is replaced by
explicit file-unreadable handling — still loud, never silent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    OAuthScopes,
)

PERMISSION_EXPLANATION = (
    "Read only your local browser history: visited URLs and timestamps. It "
    "cannot clear, edit, or write anything to your browser profile."
)

#: Capability surface for the browser-session-style (no-OAuth) model.
_SESSION_SCOPE = "browser-profile:read-history"

#: Chromium timestamps are microseconds since 1601-01-01 (Windows epoch).
_CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass
class HistoryEntry:
    url: str
    title: str
    visited_at: datetime | None = None
    summary: str = ""


@dataclass
class HistoryDigest:
    source: str
    total: int
    entries: list[HistoryEntry] = field(default_factory=list)


class HistoryTransport(Protocol):
    """The small local-file surface a history connector needs (mockable)."""

    def read_recent(self, source: str, hours: int) -> list[dict]:
        """Return [{url, title, last_visit_time}] within the last `hours`."""


class LocalHistoryTransport:
    """Reads a Chromium-family `History` SQLite file read-only."""

    def read_recent(self, source: str, hours: int) -> list[dict]:
        path = Path(source)
        if not path.exists():
            raise ConnectorError(f"history file not found: {source}")
        since_chromium = int(
            (datetime.now(timezone.utc) - timedelta(hours=hours) - _CHROMIUM_EPOCH).total_seconds() * 1_000_000
        )
        try:
            # mode=ro: this connector can never write to the profile.
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ConnectorError(f"unreadable history file: {exc}") from exc
        try:
            rows = conn.execute(
                "SELECT url, title, last_visit_time FROM urls "
                "WHERE last_visit_time >= ? ORDER BY last_visit_time DESC LIMIT 500",
                (since_chromium,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ConnectorError(f"unreadable history schema: {exc}") from exc
        finally:
            conn.close()
        return [
            {"url": url or "", "title": title or url or "", "last_visit_time": last_visit_time}
            for url, title, last_visit_time in rows
        ]


def _dt_from_chromium(epoch_micros: int | None) -> datetime | None:
    if epoch_micros is None:
        return None
    try:
        return _CHROMIUM_EPOCH + timedelta(microseconds=int(epoch_micros))
    except (ValueError, OverflowError):
        return None


def _summary(title: str, url: str) -> str:
    return f"{title} — {url}" if title and title != url else url


class HistoryConnector(AbstractConnector):
    """Read-only local-browser history integration. Tools: history.recent."""

    service = "history"
    auth_url = ""
    token_url = ""
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["history.recent"]

    def __init__(self, vault, transport: HistoryTransport | None = None, **_ignored) -> None:
        super().__init__(vault, "")
        self.transport = transport or LocalHistoryTransport()

    def declared_scopes(self) -> OAuthScopes:
        # No OAuth: capability is local file read, surfaced read-only.
        return OAuthScopes(read=[_SESSION_SCOPE], write=[])

    # OAuth hooks required by the base template but unused in this model.
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        raise ConnectorError("history uses a local profile file, not OAuth")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        raise ConnectorError("history uses a local profile file, not OAuth")

    def refresh_access_token(self, refresh_token: str) -> dict:
        raise ConnectorError("history re-reads the local file, not OAuth refresh")

    def revoke(self, access_token: str) -> None:
        raise ConnectorError("history forgets the local file, not OAuth revoke")

    # ── snapshot lifecycle (vault-backed) ─────────────────────────────────
    def connect_snapshot(self, source: str) -> dict:
        """Point the connector at a history file (encrypted path in vault)."""
        # Validate the source is a readable file BEFORE trusting it.
        if not Path(source).exists():
            raise ConnectorError(f"history file not found: {source}")
        self._vault.save(self.service, source, {"source": source}, scopes=[_SESSION_SCOPE])
        self._audit(source, "connected", {"connector": self.service, "kind": "local-snapshot"})
        return {"connector": self.service, "source": source, "scopes": self._scopes.read}

    def disconnect_snapshot(self, source: str) -> None:
        self._vault.require_entry(self.service, source)
        self._vault.delete(self.service, source)
        self._audit(source, "disconnected", {"connector": self.service})

    # ── read path (observe-tier, transparent) ─────────────────────────────
    def recent_history(self, source: str, hours: int = 72, max_results: int = 50) -> HistoryDigest:
        entry = self._vault.require_entry(self.service, source)
        if entry.get("expired"):
            raise ConnectorError(f"{source} snapshot marked expired — reconnect required")
        try:
            records = self.transport.read_recent(source, hours)
        except ConnectorError:
            raise  # loud, not silent
        entries = [
            HistoryEntry(
                url=r["url"],
                title=r["title"],
                visited_at=_dt_from_chromium(r.get("last_visit_time")),
                summary=_summary(r["title"], r["url"]),
            )
            for r in records
        ]
        return HistoryDigest(
            source=source,
            total=len(entries),
            entries=entries[:max_results],
        )
