"""
Browser bookmarks connector — READ-ONLY (Phase 6, order: … → Browser
bookmarks/history → …).

Uses the local browser's bookmarks store (an on-device profile file), NOT a
remote account — so there is no OAuth and no network token. The connector
reads a bookmark snapshot file (e.g. the Chrome/Edge `Bookmarks` JSON on the
laptop), encrypts the file path in the vault, and produces a searchable digest
of bookmarks. Write scopes are empty: adding, editing, or deleting a bookmark
is intentionally unregistered and blocked by the permission engine.

Because there is no OAuth, connect/refresh/revoke mirror the base template but
operate on the local snapshot: connect points the connector at a bookmarks
file, disconnect forgets it. Rate-limit / expired-session handling is replaced
by explicit file-unreadable handling — still loud, never silent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from integrations.base import (
    AbstractConnector,
    ConnectorError,
    OAuthScopes,
)

PERMISSION_EXPLANATION = (
    "Read only your browser bookmarks: titles, URLs, and folders. It cannot "
    "add, edit, or delete bookmarks."
)

#: Capability surface for the browser-session-style (no-OAuth) model.
_SESSION_SCOPE = "browser-profile:read-bookmarks"


@dataclass
class Bookmark:
    url: str
    title: str
    folder: str
    added_at: datetime | None = None
    summary: str = ""


@dataclass
class BookmarksDigest:
    source: str
    total: int
    entries: list[Bookmark] = field(default_factory=list)


class BookmarksTransport(Protocol):
    """The small local-file surface a bookmarks connector needs (mockable)."""

    def read_snapshot(self, source: str) -> list[dict]:
        """Parse the bookmarks file → list of {url, title, folder, dateAdded}."""


class LocalBookmarksTransport:
    """Reads a Chromium-family `Bookmarks` JSON file on the laptop."""

    def read_snapshot(self, source: str) -> list[dict]:
        path = Path(source)
        if not path.exists():
            raise ConnectorError(f"bookmarks file not found: {source}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ConnectorError(f"unreadable bookmarks file: {exc}") from exc
        roots = data.get("roots", {})
        records: list[dict] = []
        # Chromium nests each top-level folder by name under `roots`.
        for value in roots.values():
            records.extend(_walk_bookmark_tree(value))
        return records


def _walk_bookmark_tree(node: dict, folder: str = "") -> list[dict]:
    """Flatten a Chromium bookmark tree into plain records."""
    out: list[dict] = []
    if node.get("type") == "url":
        out.append({
            "url": node.get("url", ""),
            "title": node.get("name", node.get("url", "")),
            "folder": folder,
            "dateAdded": node.get("dateAdded"),
        })
    else:
        children = node.get("children", []) or []
        name = node.get("name", folder)
        for child in children:
            out.extend(_walk_bookmark_tree(child, folder=name))
    return out


def _dt_from_chromium(epoch_micros: str | None) -> datetime | None:
    if not epoch_micros:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_micros) / 1_000_000, tz=timezone.utc)
    except (ValueError, OverflowError):
        return None


def _summary(title: str, url: str, folder: str) -> str:
    return f"{title} ({folder}) — {url}" if folder else f"{title} — {url}"


class BookmarksConnector(AbstractConnector):
    """Read-only local-browser bookmarks integration. Tools: bookmarks.search."""

    service = "bookmarks"
    auth_url = ""
    token_url = ""
    permission_explanation = PERMISSION_EXPLANATION
    tools = ["bookmarks.search"]

    def __init__(self, vault, transport: BookmarksTransport | None = None, **_ignored) -> None:
        super().__init__(vault, "")
        self.transport = transport or LocalBookmarksTransport()

    def declared_scopes(self) -> OAuthScopes:
        # No OAuth: capability is local file read, surfaced read-only.
        return OAuthScopes(read=[_SESSION_SCOPE], write=[])

    # OAuth hooks required by the base template but unused in this model.
    def authorize_uri(self, state: str, redirect_uri: str) -> str:
        raise ConnectorError("bookmarks uses a local profile file, not OAuth")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        raise ConnectorError("bookmarks uses a local profile file, not OAuth")

    def refresh_access_token(self, refresh_token: str) -> dict:
        raise ConnectorError("bookmarks re-reads the local file, not OAuth refresh")

    def revoke(self, access_token: str) -> None:
        raise ConnectorError("bookmarks forgets the local file, not OAuth revoke")

    # ── snapshot lifecycle (vault-backed) ─────────────────────────────────
    def connect_snapshot(self, source: str) -> dict:
        """Point the connector at a bookmarks file (encrypted path in vault)."""
        # Validate the source is a readable file BEFORE trusting it.
        if not Path(source).exists():
            raise ConnectorError(f"bookmarks file not found: {source}")
        self._vault.save(self.service, source, {"source": source}, scopes=[_SESSION_SCOPE])
        self._audit(source, "connected", {"connector": self.service, "kind": "local-snapshot"})
        return {"connector": self.service, "source": source, "scopes": self._scopes.read}

    def disconnect_snapshot(self, source: str) -> None:
        self._vault.require_entry(self.service, source)
        self._vault.delete(self.service, source)
        self._audit(source, "disconnected", {"connector": self.service})

    # ── read path (observe-tier, transparent) ─────────────────────────────
    def search_bookmarks(self, source: str, query: str = "", max_results: int = 50) -> BookmarksDigest:
        entry = self._vault.require_entry(self.service, source)
        if entry.get("expired"):
            raise ConnectorError(f"{source} snapshot marked expired — reconnect required")
        try:
            records = self.transport.read_snapshot(source)
        except ConnectorError:
            raise  # loud, not silent
        q = (query or "").strip().lower()
        bookmarks: list[Bookmark] = []
        for r in records:
            title, url, folder = r["title"], r["url"], r["folder"]
            if q and q not in f"{title} {url} {folder}".lower():
                continue
            bookmarks.append(
                Bookmark(
                    url=url,
                    title=title,
                    folder=folder,
                    added_at=_dt_from_chromium(r.get("dateAdded")),
                    summary=_summary(title, url, folder),
                )
            )
        return BookmarksDigest(
            source=source,
            total=len(bookmarks),
            entries=bookmarks[:max_results],
        )