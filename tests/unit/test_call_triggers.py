"""Tests for user-defined proactive call triggers (Phase 8.5, item 10e)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from packages.shared.call_triggers import (
    CallTriggerStore,
    TriggerValidationError,
)


@pytest.fixture
def store(tmp_path):
    return CallTriggerStore(tmp_path / "call_triggers.json")


class TestValidation:
    def test_rejects_unknown_kind(self, store):
        with pytest.raises(TriggerValidationError, match="kind"):
            store.create(kind="whenever", at="19:00")

    def test_time_requires_hhmm(self, store):
        with pytest.raises(TriggerValidationError, match="HH:MM"):
            store.create(kind="time")
        with pytest.raises(TriggerValidationError, match="24-hour"):
            store.create(kind="time", at="7pm")

    def test_normalises_hour(self, store):
        trigger = store.create(kind="time", at="07:05")
        assert trigger.at == "07:05"
        assert store.list() == [trigger]

    def test_task_requires_nonempty_match(self, store):
        with pytest.raises(TriggerValidationError, match="match"):
            store.create(kind="task_completed")


class TestTimeTriggers:
    def test_due_within_grace_only(self, store):
        trigger = store.create(kind="time", at="19:00")
        now = datetime(2026, 9, 17, 19, 5, tzinfo=timezone.utc)

        assert [m.trigger.trigger_id for m in store.due(now=now)] == [trigger.trigger_id]

        late = now + timedelta(minutes=6)
        assert store.due(now=late) == []

    def test_not_due_before_scheduled_minute(self, store):
        store.create(kind="time", at="19:00")
        now = datetime(2026, 9, 17, 18, 59, tzinfo=timezone.utc)
        assert store.due(now=now) == []

    def test_one_fire_per_day(self, store):
        trigger = store.create(kind="time", at="19:00")
        first = datetime(2026, 9, 17, 19, 1, tzinfo=timezone.utc)
        assert store.due(now=first)

        store.mark_fired(trigger.trigger_id, now=first)
        assert store.due(now=first + timedelta(minutes=1)) == []

        next_day = first + timedelta(days=1)
        matches = store.due(now=next_day)
        assert [m.trigger.trigger_id for m in matches] == [trigger.trigger_id]

    def test_due_is_pure_until_marked(self, store):
        trigger = store.create(kind="time", at="19:00")
        now = datetime(2026, 9, 17, 19, 1, tzinfo=timezone.utc)
        assert store.due(now=now)
        assert store.due(now=now)
        assert store.get(trigger.trigger_id).last_fired_at is None


class TestTaskTriggers:
    @staticmethod
    def _task(task_id="t1", request="Deploy the new build", status="completed", updated=None):
        task = {
            "task_id": task_id,
            "request": request,
            "status": status,
            "updated_at": updated,
        }
        if updated is None:
            task.pop("updated_at")
        return task

    def test_matches_case_insensitive_substring(self, store):
        trigger = store.create(kind="task_completed", match="deploy")
        created = datetime.now().astimezone()
        task = self._task(updated=(created + timedelta(minutes=1)).isoformat())

        matches = store.due(
            now=created + timedelta(minutes=1), completed_tasks=[task]
        )
        assert [m.trigger.trigger_id for m in matches] == [trigger.trigger_id]
        assert matches[0].task_id == "t1"
        assert matches[0].task_request == "Deploy the new build"

    def test_ignores_non_completed_status(self, store):
        store.create(kind="task_completed", match="deploy")
        created = datetime.now().astimezone()
        task = self._task(
            status="running", updated=(created + timedelta(minutes=1)).isoformat()
        )
        assert store.due(completed_tasks=[task]) == []

    def test_ignores_task_completed_before_trigger(self, store):
        store.create(kind="task_completed", match="deploy")
        created = datetime.fromisoformat(store.get(store.list()[0].trigger_id).created_at)
        task = self._task(updated=(created - timedelta(minutes=1)).isoformat())
        assert store.due(completed_tasks=[task]) == []

    def test_mark_fired_prevents_duplicate_for_same_task(self, store):
        trigger = store.create(kind="task_completed", match="deploy")
        created = datetime.now().astimezone()
        task = self._task(updated=(created + timedelta(minutes=1)).isoformat())

        assert store.due(completed_tasks=[task])
        store.mark_fired(trigger.trigger_id, task_id="t1")
        assert store.due(completed_tasks=[task]) == []

        other = self._task(task_id="t2", updated=(created + timedelta(minutes=2)).isoformat())
        assert [m.task_id for m in store.due(completed_tasks=[other])] == ["t2"]

    def test_one_shot_disables_after_fire(self, store):
        trigger = store.create(kind="task_completed", match="deploy", one_shot=True)
        created = datetime.now().astimezone()
        task = self._task(updated=(created + timedelta(minutes=1)).isoformat())

        store.mark_fired(trigger.trigger_id, task_id="t1")
        assert store.get(trigger.trigger_id).enabled is False
        assert store.due(completed_tasks=[task]) == []


class TestPersistence:
    def test_reload_from_disk(self, store, tmp_path):
        created = store.create(kind="time", at="19:00", label="Evening check-in")
        reloaded = CallTriggerStore(tmp_path / "call_triggers.json")
        assert [t.trigger_id for t in reloaded.list()] == [created.trigger_id]
        assert reloaded.get(created.trigger_id).label == "Evening check-in"
        assert not (tmp_path / "call_triggers.json.tmp").exists()

    def test_atomic_replace_writes_valid_json(self, store, tmp_path):
        store.create(kind="task_completed", match="deploy")
        store.delete(store.list()[0].trigger_id)
        raw = json.loads((tmp_path / "call_triggers.json").read_text("utf-8"))
        assert raw == []

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "call_triggers.json"
        path.write_text("{not json", "utf-8")
        assert CallTriggerStore(path).list() == []
