"""Task-history adapter and repeated-sequence mining (Phase 7.4)."""

from datetime import UTC, datetime, timedelta

from packages.shared.routine_miner import RoutineMiner, TaskHistoryAdapter
from packages.shared.schemas import Task, TaskStatus, TaskStep


class FakeManager:
    def __init__(self, tasks):
        self.tasks = tasks

    def list_tasks(self, limit=50):
        return self.tasks[:limit]


def _task(request, tools, hours_ago=0):
    return Task(
        request=request,
        status=TaskStatus.COMPLETED,
        steps=[
            TaskStep(agent="system", tool=tool, risk_level="observe") for tool in tools
        ],
        created_at=datetime.now(UTC) - timedelta(hours=hours_ago),
    )


def test_adapter_flattens_steps_into_ordered_sequence():
    tasks = [
        _task("open chrome then mute", ["system.open_app", "smart_home.control"], 1),
        _task("open chrome then mute", ["system.open_app", "smart_home.control"], 2),
        _task("open chrome then mute", ["system.open_app", "smart_home.control"], 3),
    ]
    records = TaskHistoryAdapter(FakeManager(tasks)).list_tasks()
    assert len(records) == 3
    assert records[0]["sequence"] == ["system.open_app", "smart_home.control"]
    assert records[0]["tool"] == "system.open_app"
    assert records[0]["status"] == "completed"


def test_repeated_sequences_are_suggested_as_prepare_patterns():
    tasks = [
        _task("open chrome and mute", ["system.open_app", "smart_home.control"], i)
        for i in range(3)
    ]
    patterns = RoutineMiner(
        task_store=TaskHistoryAdapter(FakeManager(tasks)), min_occurrences=3
    ).find_routines()
    sequences = [p for p in patterns if len(p.sequence) == 2]
    assert sequences, patterns
    assert sequences[0].sequence == ["system.open_app", "smart_home.control"]
    assert "system.open_app" in sequences[0].description
    assert 0 < sequences[0].confidence <= 1
    assert sequences[0].to_dict()["sequence"] == ["system.open_app", "smart_home.control"]


def test_single_step_tasks_do_not_fabricate_a_sequence():
    tasks = [_task("open chrome", ["system.open_app"], i) for i in range(3)]
    patterns = RoutineMiner(
        task_store=TaskHistoryAdapter(FakeManager(tasks)), min_occurrences=3
    ).find_routines()
    assert all(len(p.sequence) != 2 for p in patterns)


def test_window_drops_old_history():
    old = Task(
        request="open chrome",
        status=TaskStatus.COMPLETED,
        steps=[TaskStep(agent="system", tool="system.open_app", risk_level="observe")],
        created_at=datetime.now(UTC) - timedelta(days=90),
    )
    records = TaskHistoryAdapter(FakeManager([old])).list_tasks()
    assert len(records) == 1  # adapter itself does not filter
    miner = RoutineMiner(task_store=TaskHistoryAdapter(FakeManager([old])), min_occurrences=1)
    # Historical records outside the window are ignored by the miner.
    assert miner._get_recent_tasks() == []
