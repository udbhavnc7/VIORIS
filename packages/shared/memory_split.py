"""
Memory Split (Phase 7.2).

Splits the memory store into two retrieval paths:

  - **Episodic**: timestamped events — what happened, when. Task history,
    conversation turns, actions taken. Optimized for chronological queries
    ("what did I ask you yesterday", "what happened at 3pm").

  - **Semantic**: facts and preferences — what you know or prefer. Contact
    details, settings, learned routines. Optimized for lookup queries
    ("what's my WiFi password", "who is my manager").

The split improves retrieval quality because "what happened" and "what do I
know" hit fundamentally different search strategies. Without the split, both
query types hit the same bucket and both get worse.

This module wraps the existing MemoryStore, adding:
  1. A `memory_type` field to distinguish episodic vs semantic records.
  2. Separate query methods for each type.
  3. Category-to-type mapping for backward compatibility.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Iterator

from packages.shared.schemas import Memory, MemoryCategory

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


# Map existing categories to memory types
CATEGORY_TO_TYPE: dict[MemoryCategory, MemoryType] = {
    # Episodic: things that happened at a specific time
    MemoryCategory.ROUTINES: MemoryType.EPISODIC,
    # Semantic: things you know or prefer
    MemoryCategory.PREFERENCES: MemoryType.SEMANTIC,
    MemoryCategory.PEOPLE: MemoryType.SEMANTIC,
    MemoryCategory.PROJECTS: MemoryType.SEMANTIC,
    MemoryCategory.SENSITIVE: MemoryType.SEMANTIC,
}

# Default mapping for new categories not explicitly listed
DEFAULT_MEMORY_TYPE = MemoryType.SEMANTIC


def classify_memory_type(category: MemoryCategory) -> MemoryType:
    """Determine whether a memory category is episodic or semantic."""
    return CATEGORY_TO_TYPE.get(category, DEFAULT_MEMORY_TYPE)


@dataclass
class EpisodicQuery:
    """Query parameters for episodic memory retrieval."""

    after: datetime | None = None  # only events after this time
    before: datetime | None = None  # only events before this time
    category: MemoryCategory | None = None  # optional category filter
    limit: int = 20  # max results
    keyword: str | None = None  # optional keyword filter on content


@dataclass
class SemanticQuery:
    """Query parameters for semantic memory retrieval."""

    category: MemoryCategory | None = None  # optional category filter
    keyword: str | None = None  # optional keyword filter on content
    limit: int = 50  # max results


@dataclass
class MemorySplitter:
    """Provides split retrieval over the memory store.

    Wraps an existing MemoryStore and adds episodic/semantic query paths.
    Does not duplicate data — reads the same rows but filters differently.
    """

    store: object  # MemoryStore instance

    def query_episodic(self, query: EpisodicQuery) -> list[Memory]:
        """Retrieve episodic memories (things that happened).

        Returns memories sorted by creation time (newest first), filtered
        by time range and optional category/keyword.
        """
        all_memories = self._get_all()

        # Filter to episodic type
        results = [
            m for m in all_memories
            if classify_memory_type(m.category) == MemoryType.EPISODIC
        ]

        # Apply time range filter
        if query.after:
            results = [m for m in results if m.created_at >= query.after]
        if query.before:
            results = [m for m in results if m.created_at <= query.before]

        # Apply category filter
        if query.category:
            results = [m for m in results if m.category == query.category]

        # Apply keyword filter
        if query.keyword:
            kw = query.keyword.lower()
            results = [m for m in results if kw in m.content.lower()]

        # Sort by creation time, newest first
        results.sort(key=lambda m: m.created_at, reverse=True)

        return results[: query.limit]

    def query_semantic(self, query: SemanticQuery) -> list[Memory]:
        """Retrieve semantic memories (things you know/prefer).

        Returns memories sorted by confidence (highest first), optionally
        filtered by category and keyword.
        """
        all_memories = self._get_all()

        # Filter to semantic type
        results = [
            m for m in all_memories
            if classify_memory_type(m.category) == MemoryType.SEMANTIC
        ]

        # Apply category filter
        if query.category:
            results = [m for m in results if m.category == query.category]

        # Apply keyword filter
        if query.keyword:
            kw = query.keyword.lower()
            results = [m for m in results if kw in m.content.lower()]

        # Sort by confidence, highest first
        results.sort(key=lambda m: m.confidence, reverse=True)

        return results[: query.limit]

    def query_by_type(self, memory_type: MemoryType, keyword: str | None = None) -> list[Memory]:
        """Generic query by memory type with optional keyword."""
        if memory_type == MemoryType.EPISODIC:
            return self.query_episodic(EpisodicQuery(keyword=keyword))
        return self.query_semantic(SemanticQuery(keyword=keyword))

    def stats(self) -> dict:
        """Return counts of episodic vs semantic memories."""
        all_memories = self._get_all()
        episodic = sum(1 for m in all_memories if classify_memory_type(m.category) == MemoryType.EPISODIC)
        semantic = sum(1 for m in all_memories if classify_memory_type(m.category) == MemoryType.SEMANTIC)
        return {
            "total": len(all_memories),
            "episodic": episodic,
            "semantic": semantic,
        }

    def _get_all(self) -> list[Memory]:
        """Retrieve all memories from the underlying store."""
        # The MemoryStore.list() method returns all memories when category=None
        return self.store.list(category=None)
