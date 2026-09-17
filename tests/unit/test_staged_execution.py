"""Tests for Phase 8.3 — reversible-by-default execution."""

import threading
import time

import pytest

from services.task_runner.app.staged_execution import (
    StageStatus,
    StagedAction,
    StagedActionStore,
)


@pytest.fixture
def store():
    return StagedActionStore()


class TestStage:
    def test_stage_creates_action(self, store):
        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete important.txt",
            diff_card={"path": "/docs/important.txt"},
            execute_fn=lambda: None,
        )
        assert action.status == StageStatus.STAGED
        assert action.tool == "file.delete"
        assert action.task_id == "t1"

    def test_stage_returns_unique_ids(self, store):
        a1 = store.stage(tool="a", task_id="t", step_id="s", description="", diff_card={}, execute_fn=lambda: None)
        a2 = store.stage(tool="b", task_id="t", step_id="s", description="", diff_card={}, execute_fn=lambda: None)
        assert a1.stage_id != a2.stage_id

    def test_list_pending(self, store):
        store.stage(tool="a", task_id="t", step_id="s", description="", diff_card={}, execute_fn=lambda: None)
        store.stage(tool="b", task_id="t", step_id="s", description="", diff_card={}, execute_fn=lambda: None)
        assert len(store.list_pending()) == 2


class TestCancel:
    def test_cancel_before_execute(self, store):
        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=lambda: None,
            execute_after_seconds=10,
        )
        assert store.cancel(action.stage_id) is True
        assert action.status == StageStatus.CANCELLED

    def test_cancel_after_execute_fails(self, store):
        executed = threading.Event()

        def slow_execute():
            executed.set()

        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=slow_execute,
            execute_after_seconds=0.05,
        )
        executed.wait(timeout=2)
        time.sleep(0.1)
        assert store.cancel(action.stage_id) is False

    def test_cancel_nonexistent_fails(self, store):
        assert store.cancel("stage_nonexistent") is False


class TestAutoExecute:
    def test_auto_executes_after_window(self, store):
        executed = threading.Event()

        def on_execute():
            executed.set()

        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=on_execute,
            execute_after_seconds=0.05,
        )
        executed.wait(timeout=2)
        assert executed.is_set()
        assert action.status == StageStatus.EXECUTED

    def test_does_not_execute_if_cancelled(self, store):
        executed = threading.Event()

        def on_execute():
            executed.set()

        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=on_execute,
            execute_after_seconds=0.5,
        )
        store.cancel(action.stage_id)
        executed.wait(timeout=1)
        assert not executed.is_set()


class TestUndo:
    def test_undo_within_window(self, store):
        undo_called = threading.Event()

        def on_execute():
            pass

        def on_undo():
            undo_called.set()

        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=on_execute,
            undo_fn=on_undo,
            execute_after_seconds=0.05,
            undo_window_seconds=5,
        )
        # Wait for execution
        time.sleep(0.2)
        assert action.status == StageStatus.EXECUTED

        # Undo within window
        assert store.undo(action.stage_id) is True
        assert action.status == StageStatus.UNDONE

    def test_undo_after_window_fails(self, store):
        def on_execute():
            pass

        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=on_execute,
            undo_fn=lambda: None,
            execute_after_seconds=0.05,
            undo_window_seconds=0.1,
        )
        # Wait for execution + undo window to expire
        time.sleep(0.5)
        assert store.undo(action.stage_id) is False

    def test_undo_staged_action_fails(self, store):
        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=lambda: None,
            execute_after_seconds=10,
        )
        assert store.undo(action.stage_id) is False


class TestListUndoable:
    def test_lists_executed_actions(self, store):
        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=lambda: None,
            execute_after_seconds=0.05,
        )
        time.sleep(0.2)
        undoable = store.list_undoable()
        assert len(undoable) == 1
        assert undoable[0].stage_id == action.stage_id


class TestCleanup:
    def test_cleanup_removes_terminal(self, store):
        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=lambda: None,
            execute_after_seconds=0.05,
        )
        time.sleep(0.2)
        # Mark as undone
        store.undo(action.stage_id)
        removed = store.cleanup_expired()
        assert removed == 1
        assert store.get(action.stage_id) is None


class TestEdgeCases:
    def test_execute_fn_exception_marks_cancelled(self, store):
        def bad_execute():
            raise RuntimeError("disk full")

        action = store.stage(
            tool="file.delete",
            task_id="t1",
            step_id="s1",
            description="Delete",
            diff_card={},
            execute_fn=bad_execute,
            execute_after_seconds=0.05,
        )
        time.sleep(0.2)
        assert action.status == StageStatus.CANCELLED

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("stage_nonexistent") is None
