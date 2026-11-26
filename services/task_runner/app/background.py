"""Background step execution (Phase 9.1).

Steps run inline in the API process by default — zero config, no Redis needed.
With ``VIORIS_TASK_QUEUE=celery`` dispatch hands the runnable part of a task to
a Celery worker over Redis. The worker re-opens the durable TaskManager, so the
permission gate, approval ledger and hash-chained audit are enforced again in
the worker process rather than trusted from the API request.

Worker:
    celery -A services.task_runner.app.background:celery_app worker --loglevel=info

The API never marks a step complete itself when queued: status comes from the
TaskManager's SQLite record after the worker runs, so the approval gate stays
the single source of truth in both modes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from packages.shared.infrastructure import TaskQueue, TaskStatus as QueueStatus
from packages.shared.schemas import TaskStatus

logger = logging.getLogger(__name__)

RUN_TASK = "task_runner.run_task"
_CELERY_VALUES = {"1", "true", "celery", "redis"}
_TERMINAL = {"completed", "failed", "cancelled", "stopped", "paused", "waiting_approval"}
MAX_WORKER_STEPS = 64
# Short enough not to block a legitimate retry of the same step, long enough
# to absorb a double-tap or a stale caller re-posting /run in the same burst.
_DEDUPE_SECONDS = 1.0

_queue: TaskQueue | None = None
_queue_lock = threading.Lock()
_enqueued: dict[str, float] = {}
_enqueued_lock = threading.Lock()


def celery_mode() -> bool:
    """Celery is opt-in: a Redis URL alone is not consent to use a broker."""
    return os.getenv("VIORIS_TASK_QUEUE", "").strip().lower() in _CELERY_VALUES


def run_task(task_id: str) -> dict:
    """Worker entrypoint: drain the currently runnable steps of one task.

    Each iteration goes through TaskManager.run_next_step, which re-checks the
    frozen permission registry and the approval ledger. A step waiting for
    approval stops the loop — a queued job can never fire it.
    """
    from .api import _execute, get_manager

    mgr = get_manager()
    last: dict = {"task_id": task_id, "status": "unknown", "ok": False}
    for _ in range(MAX_WORKER_STEPS):
        task, outcome = mgr.run_next_step(task_id, _execute)
        step = outcome.step
        last = {
            "task_id": task_id,
            "status": task.status.value,
            "step_id": step.step_id if step else None,
            "ok": outcome.ok,
            "error": outcome.error,
        }
        if step is None or step.status == TaskStatus.WAITING_APPROVAL:
            break
        if task.status.value in _TERMINAL:
            break
    return last


def get_queue() -> TaskQueue:
    """Create (once) the queue used for dispatch and by the worker process."""
    global _queue
    if _queue is None:
        with _queue_lock:
            if _queue is None:
                q = TaskQueue()
                q.enable_celery()  # no-op when celery is not installed
                if q.app is not None:
                    q.register(RUN_TASK, run_task)
                _queue = q
    return _queue


def reset_queue() -> None:
    """Drop cached queue state (tests / config changes)."""
    global _queue
    with _queue_lock:
        _queue = None
    with _enqueued_lock:
        _enqueued.clear()


def dispatch_step(
    task_id: str,
    *,
    queue: TaskQueue | None = None,
    run_local: Callable | None = None,
) -> dict:
    """Run one step inline, or enqueue the whole runnable part of the task.

    Returns a dict with ``queued``, the current ``task`` and, for inline runs,
    the ``outcome``. Never does both: an enqueue failure is reported, not
    retried inline, because the job may already be on the broker.
    """
    from .api import _execute, get_manager

    mgr = get_manager()
    task = mgr.get(task_id)
    runnable = next((s for s in task.steps if s.status == TaskStatus.CREATED), None)
    q = queue or get_queue()

    if not (celery_mode() and q._use_celery and q.app is not None):
        if celery_mode():
            logger.warning("VIORIS_TASK_QUEUE=celery but celery is unavailable; running inline")
        execute = run_local or (lambda step, task=None: _execute(step, task=task))
        task, outcome = mgr.run_next_step(task_id, execute)
        return {"queued": False, "task": task, "outcome": outcome}

    if runnable is None or task.status not in (TaskStatus.CREATED, TaskStatus.RUNNING):
        return {"queued": False, "task": task, "outcome": None, "error": "no runnable step"}

    key = f"{task_id}:{runnable.step_id}"
    now = time.monotonic()
    with _enqueued_lock:
        if now - _enqueued.get(key, 0) < _DEDUPE_SECONDS:
            return {"queued": True, "task": task, "outcome": None, "duplicate": True}

    submitted = q.submit(RUN_TASK, task_id, task_id=f"vioris-{key}")
    if submitted.status == QueueStatus.FAILURE:
        return {"queued": False, "task": task, "outcome": None, "error": submitted.error}

    with _enqueued_lock:
        _enqueued[key] = now
    mgr.append_audit(
        "system",
        "queued",
        {
            "task": task_id,
            "step": runnable.step_id,
            "tool": runnable.tool,
            "broker": q.broker_url,
        },
        task_id,
    )
    return {"queued": True, "task": task, "outcome": None, "step": runnable.step_id}


celery_app = get_queue().app
