"""Memory store unit tests: category separation, sensitive opt-in gate, delete."""

import pytest

from packages.shared.schemas import MemoryCategory

from services.memory_service.app.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "memory.db")
    yield s
    s.close()


def test_add_and_get_roundtrip(store):
    res = store.store_and_user_opt_in(
        MemoryCategory.PEOPLE, "Alice prefers morning meetings", "user said on 2026-08-01"
    )
    assert res.ok
    mem = res.memory
    assert mem.category == MemoryCategory.PEOPLE
    assert mem.source == "user said on 2026-08-01"
    assert mem.user_opted_in is False
    assert mem.created_at is not None

    got = store.get(mem.memory_id)
    assert got.ok and got.memory.content == "Alice prefers morning meetings"


def test_list_by_category_is_separated(store):
    store.add(MemoryCategory.PREFERENCES, "prefers dark mode", "sys")
    store.add(MemoryCategory.PROJECTS, "working on Vioris", "sys")
    store.add(MemoryCategory.ROUTINES, "morning coffee", "sys")

    prefs = store.list(MemoryCategory.PREFERENCES)
    projs = store.list(MemoryCategory.PROJECTS)
    assert len(prefs) == 1 and prefs[0].category == MemoryCategory.PREFERENCES
    assert len(projs) == 1
    assert len(store.list()) == 3


def test_sensitive_write_requires_opt_in(store):
    res = store.add(MemoryCategory.SENSITIVE, "bank password hint", "user")
    assert not res.ok
    assert "user_opted_in" in res.error
    assert store.list(MemoryCategory.SENSITIVE) == []


def test_sensitive_write_with_opt_in_succeeds(store):
    res = store.store_and_user_opt_in(
        MemoryCategory.SENSITIVE, "bank", "user", user_opted_in=True
    )
    assert res.ok
    assert res.memory.user_opted_in is True


def test_correct_updates_content_and_keeps_source(store):
    mem = store.add(MemoryCategory.PREFERENCES, "old", "sys").memory
    res = store.correct(mem.memory_id, "new")
    assert res.ok
    assert res.memory.content == "new"
    assert res.memory.memory_id == mem.memory_id
    assert res.memory.created_at == mem.created_at


def test_delete_removes_by_id(store):
    mem = store.add(MemoryCategory.PEOPLE, "x", "sys").memory
    res = store.delete(mem.memory_id)
    assert res.ok and res.detail["deleted"] is True
    assert store.get(mem.memory_id).ok is False


def test_delete_missing_is_idempotent(store):
    res = store.delete("mem_nonexistent")
    assert res.ok is False


def test_confidence_bounds_enforced(store):
    with pytest.raises(ValueError):
        store.add(MemoryCategory.PREFERENCES, "c", "sys", confidence=1.5)