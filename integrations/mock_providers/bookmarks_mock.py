"""
Scripted mock Bookmarks transport for tests (Phase 6).

Implements the BookmarksTransport protocol with a canned bookmark tree and
injectable failures, so connector tests never touch a real browser profile.
"""

from __future__ import annotations

from integrations.base import ConnectorError


class MockBookmarksTransport:
    """A Chromium-bookmarks-shaped provider with canned data + failure injection."""

    def __init__(self, tree: dict | None = None) -> None:
        self.name = "bookmarks"
        self.tree = tree if tree is not None else _DEFAULT_TREE
        self.calls: list[str] = []
        self.fail_missing: bool = False
        self.fail_corrupt: bool = False

    def read_snapshot(self, source: str) -> list[dict]:
        self.calls.append(f"read_snapshot:{source}")
        if self.fail_missing:
            raise ConnectorError(f"bookmarks file not found: {source}")
        if self.fail_corrupt:
            raise ConnectorError("unreadable bookmarks file: bad json")
        from integrations.bookmarks.connector import _walk_bookmark_tree

        roots = self.tree.get("roots", {})
        records: list[dict] = []
        for value in roots.values():
            records.extend(_walk_bookmark_tree(value))
        return records


_DEFAULT_TREE = {
    "roots": {
        "bookmark_bar": {
            "name": "Bookmarks bar",
            "type": "folder",
            "children": [
                {
                    "name": "Vioris Docs",
                    "url": "https://example.com/vioris",
                    "type": "url",
                    "dateAdded": "1720000000000000",
                },
                {
                    "name": "Agent Research",
                    "url": "https://example.com/agents",
                    "type": "url",
                    "dateAdded": "1710000000000000",
                },
            ],
        },
        "other": {
            "name": "Other bookmarks",
            "type": "folder",
            "children": [
                {
                    "name": "Recipes",
                    "url": "https://example.com/recipes",
                    "type": "url",
                    "dateAdded": "1700000000000000",
                },
            ],
        },
    }
}