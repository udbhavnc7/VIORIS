"""Tests for the memory split module (Phase 7.2)."""

import pytest
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.shared.memory_split import (
    MemoryType,
    MemorySplitter,
    EpisodicQuery,
    SemanticQuery,
    classify_memory_type,
)
from packages.shared.schemas import MemoryCategory
from services.memory_service.app.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def splitter(store):
    return MemorySplitter(store=store)


class TestPersistedCollections:
    def test_store_persists_collection_from_category(self, store):
        event = store.add(MemoryCategory.ROUTINES, "Opened Slack at 9am", "task_history").memory
        fact = store.add(MemoryCategory.PEOPLE, "Disha is my wife", "user said").memory
        assert event.memory_type == MemoryType.EPISODIC
        assert fact.memory_type == MemoryType.SEMANTIC

    def test_explicit_collection_overrides_category_mapping(self, store):
        logged = store.add(
            MemoryCategory.PROJECTS, "Shipped the build at 4pm", "task_history",
            memory_type=MemoryType.EPISODIC,
        ).memory
        assert logged.memory_type == MemoryType.EPISODIC
        assert splitter_types(store) == [MemoryType.EPISODIC]

    def test_collection_survives_reopen(self, tmp_path):
        path = tmp_path / "persist.db"
        first = MemoryStore(path)
        first.add(MemoryCategory.ROUTINES, "Ran the report", "task_history")
        first.close()
        second = MemoryStore(path)
        assert second.list(MemoryCategory.ROUTINES)[0].memory_type == MemoryType.EPISODIC
        second.close()

    def test_legacy_database_is_migrated_without_losing_history(self, tmp_path):
        path = tmp_path / "legacy.db"
        with sqlite3.connect(str(path)) as conn:
            conn.execute(
                """
                CREATE TABLE memories (
                    memory_id TEXT PRIMARY KEY, category TEXT NOT NULL,
                    content TEXT NOT NULL, source TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    user_opted_in INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    embedding BLOB
                )
                """
            )
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("mem_old1", "routines", "Ran the report", "task_history", 1.0, 0, now, now, None),
            )
            conn.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("mem_old2", "people", "Bob is my manager", "user said", 1.0, 0, now, now, None),
            )

        store = MemoryStore(path)
        assert store.get("mem_old1").memory.memory_type == MemoryType.EPISODIC
        assert store.get("mem_old2").memory.memory_type == MemoryType.SEMANTIC
        assert MemorySplitter(store=store).stats() == {"total": 2, "episodic": 1, "semantic": 1}

    def test_recategorization_moves_the_collection(self, store):
        mem = store.add(MemoryCategory.ROUTINES, "Event", "task_history").memory
        moved = store.correct(mem.memory_id, "Event", category_overwrite=MemoryCategory.PROJECTS).memory
        assert moved.memory_type == MemoryType.SEMANTIC
        assert MemorySplitter(store=store).stats()["episodic"] == 0


def splitter_types(store):
    return [m.memory_type for m in store.list()]


class TestClassifyMemoryType:
    def test_routines_are_episodic(self):
        assert classify_memory_type(MemoryCategory.ROUTINES) == MemoryType.EPISODIC

    def test_preferences_are_semantic(self):
        assert classify_memory_type(MemoryCategory.PREFERENCES) == MemoryType.SEMANTIC

    def test_people_are_semantic(self):
        assert classify_memory_type(MemoryCategory.PEOPLE) == MemoryType.SEMANTIC

    def test_projects_are_semantic(self):
        assert classify_memory_type(MemoryCategory.PROJECTS) == MemoryType.SEMANTIC

    def test_sensitive_are_semantic(self):
        assert classify_memory_type(MemoryCategory.SENSITIVE) == MemoryType.SEMANTIC


class TestEpisodicQuery:
    def test_returns_only_episodic(self, store, splitter):
        # Add episodic memory (ROUTINES)
        store.add(MemoryCategory.ROUTINES, "Open Slack at 9am daily", "task_history")
        # Add semantic memory (PREFERENCES)
        store.add(MemoryCategory.PREFERENCES, "Dark mode preferred", "user_settings")

        results = splitter.query_episodic(EpisodicQuery())
        assert len(results) == 1
        assert results[0].category == MemoryCategory.ROUTINES

    def test_time_range_after(self, store, splitter):
        now = datetime.now(UTC)
        # Add memory with default time
        store.add(MemoryCategory.ROUTINES, "Recent action", "task_history")

        # Query after a future time — should return nothing
        future = now + timedelta(hours=1)
        results = splitter.query_episodic(EpisodicQuery(after=future))
        assert len(results) == 0

    def test_time_range_before(self, store, splitter):
        now = datetime.now(UTC)
        store.add(MemoryCategory.ROUTINES, "Old action", "task_history")

        # Query before the distant past — should return nothing
        past = datetime(2000, 1, 1, tzinfo=UTC)
        results = splitter.query_episodic(EpisodicQuery(before=past))
        assert len(results) == 0

    def test_category_filter(self, store, splitter):
        store.add(MemoryCategory.ROUTINES, "Action 1", "task_history")
        store.add(MemoryCategory.ROUTINES, "Action 2", "task_history")

        results = splitter.query_episodic(EpisodicQuery(category=MemoryCategory.ROUTINES))
        assert len(results) == 2

    def test_keyword_filter(self, store, splitter):
        store.add(MemoryCategory.ROUTINES, "Opened Chrome browser", "task_history")
        store.add(MemoryCategory.ROUTINES, "Checked email inbox", "task_history")

        results = splitter.query_episodic(EpisodicQuery(keyword="chrome"))
        assert len(results) == 1
        assert "Chrome" in results[0].content

    def test_limit(self, store, splitter):
        for i in range(5):
            store.add(MemoryCategory.ROUTINES, f"Action {i}", "task_history")

        results = splitter.query_episodic(EpisodicQuery(limit=3))
        assert len(results) == 3

    def test_empty_store(self, splitter):
        results = splitter.query_episodic(EpisodicQuery())
        assert results == []


class TestSemanticQuery:
    def test_returns_only_semantic(self, store, splitter):
        store.add(MemoryCategory.ROUTINES, "Open Slack at 9am daily", "task_history")
        store.add(MemoryCategory.PREFERENCES, "Dark mode preferred", "user_settings")

        results = splitter.query_semantic(SemanticQuery())
        assert len(results) == 1
        assert results[0].category == MemoryCategory.PREFERENCES

    def test_category_filter(self, store, splitter):
        store.add(MemoryCategory.PREFERENCES, "Dark mode", "user_settings")
        store.add(MemoryCategory.PEOPLE, "John is my manager", "conversation")
        store.add(MemoryCategory.PROJECTS, "Vioris is the main project", "task_history")

        results = splitter.query_semantic(SemanticQuery(category=MemoryCategory.PEOPLE))
        assert len(results) == 1
        assert "John" in results[0].content

    def test_keyword_filter(self, store, splitter):
        store.add(MemoryCategory.PREFERENCES, "Dark mode preferred", "user_settings")
        store.add(MemoryCategory.PREFERENCES, "High volume preferred", "user_settings")

        results = splitter.query_semantic(SemanticQuery(keyword="dark"))
        assert len(results) == 1
        assert "Dark" in results[0].content

    def test_sorted_by_confidence(self, store, splitter):
        store.add(MemoryCategory.PREFERENCES, "Low confidence", "test", confidence=0.3)
        store.add(MemoryCategory.PREFERENCES, "High confidence", "test", confidence=0.9)
        store.add(MemoryCategory.PREFERENCES, "Medium confidence", "test", confidence=0.6)

        results = splitter.query_semantic(SemanticQuery())
        confidences = [m.confidence for m in results]
        assert confidences == sorted(confidences, reverse=True)

    def test_limit(self, store, splitter):
        for i in range(10):
            store.add(MemoryCategory.PREFERENCES, f"Pref {i}", "test")

        results = splitter.query_semantic(SemanticQuery(limit=5))
        assert len(results) == 5


class TestQueryByType:
    def test_episodic_via_generic(self, store, splitter):
        store.add(MemoryCategory.ROUTINES, "Action logged", "task_history")
        store.add(MemoryCategory.PREFERENCES, "Setting saved", "user_settings")

        results = splitter.query_by_type(MemoryType.EPISODIC)
        assert len(results) == 1
        assert results[0].category == MemoryCategory.ROUTINES

    def test_semantic_via_generic(self, store, splitter):
        store.add(MemoryCategory.ROUTINES, "Action logged", "task_history")
        store.add(MemoryCategory.PREFERENCES, "Setting saved", "user_settings")

        results = splitter.query_by_type(MemoryType.SEMANTIC)
        assert len(results) == 1
        assert results[0].category == MemoryCategory.PREFERENCES

    def test_keyword_via_generic(self, store, splitter):
        store.add(MemoryCategory.ROUTINES, "Opened Chrome", "task_history")
        store.add(MemoryCategory.ROUTINES, "Checked email", "task_history")

        results = splitter.query_by_type(MemoryType.EPISODIC, keyword="chrome")
        assert len(results) == 1


class TestStats:
    def test_empty_store(self, splitter):
        stats = splitter.stats()
        assert stats == {"total": 0, "episodic": 0, "semantic": 0}

    def test_mixed_store(self, store, splitter):
        store.add(MemoryCategory.ROUTINES, "Action 1", "task_history")
        store.add(MemoryCategory.ROUTINES, "Action 2", "task_history")
        store.add(MemoryCategory.PREFERENCES, "Pref 1", "user_settings")
        store.add(MemoryCategory.PEOPLE, "Person 1", "conversation")
        store.add(MemoryCategory.PROJECTS, "Project 1", "task_history")

        stats = splitter.stats()
        assert stats["total"] == 5
        assert stats["episodic"] == 2
        assert stats["semantic"] == 3
