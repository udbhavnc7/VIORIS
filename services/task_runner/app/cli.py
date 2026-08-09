"""
Task Runner CLI (Phase 2).

Lets you drive the exit test from the command line:

    # create a task with 3 steps
    # create a task with 3 steps
    python -m services.task_runner.app.cli create "demo" --steps 3
    python -m services.task_runner.app.cli run <task_id>
    python -m services.task_runner.app.cli pause <task_id>
    python -m services.task_runner.app.cli resume <task_id>
    python -m services.task_runner.app.cli retry <task_id>
    python -m services.task_runner.app.cli status <task_id>
    python -m services.task_runner.app.cli list
    # approval flow (execute/critical steps pause for a decision)
    python -m services.task_runner.app.cli approvals
    python -m services.task_runner.app.cli approve <task_id> <approval_id>
    python -m services.task_runner.app.cli reject <task_id> <approval_id>

The `execute` callback is a stand-in tool executor; Phase 3 replaces it with the
real computer/browser agents. A step whose tool is 'demo.fail' raises so the
retry path is exercisable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from packages.shared.permission_engine import register_phase1_tools, register_phase2_tools
from packages.shared.schemas import RiskTier, TaskStep

from .manager import TaskManager

DEFAULT_DB = Path("vioris_data/task_runner.db")


def _bootstrap_registry() -> None:
    """Populate the static permission registry (idempotent), exactly like a
    production entrypoint. The approval diff card reads registered fields, so
    the CLI process must register tools or execute/critical cards render empty."""
    register_phase1_tools()
    register_phase2_tools()


def default_execute(step: TaskStep) -> dict:
    """Stand-in tool executor for the CLI. Phase 3 replaces this with real agents.

    Every run appends to a local ledger keyed by idempotency_key so tests can
    prove a retry does not duplicate side effects.
    """
    ledger = Path("vioris_data/task_runner_ledger.jsonl")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    record = {"step_id": step.step_id, "idempotency_key": step.idempotency_key, "tool": step.tool}
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    if step.tool == "demo.fail":
        raise RuntimeError("demo.fail step deliberately fails")
    return {"ok": True, "tool": step.tool}


def make_steps(count: int, fail_step: int | None = None) -> list[TaskStep]:
    steps = []
    for i in range(count):
        tool = "demo.fail" if (fail_step is not None and i == fail_step) else "system.open_app"
        steps.append(TaskStep(agent="computer", tool=tool, risk_level=RiskTier.OBSERVE))
    return steps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vioris-task-runner")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="create a task")
    p.add_argument("request")
    p.add_argument("--steps", type=int, default=1)
    p.add_argument("--fail-step", type=int, default=None)
    p.add_argument("--task-id", default=None)

    for name, help_text in [
        ("run", "run the next pending step"),
        ("pause", "pause the task"),
        ("resume", "resume the task"),
        ("cancel", "cancel the task"),
        ("retry", "retry the first failed step"),
        ("status", "show task status"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("task_id", nargs="?", default=None)

    p = sub.add_parser("list", help="list tasks")

    p = sub.add_parser("approvals", help="list pending approval requests")
    p.add_argument("task_id", nargs="?", default=None)

    for name, help_text in [
        ("approve", "approve a pending approval request"),
        ("reject", "reject a pending approval request"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("task_id", nargs="?", default=None)
        p.add_argument("approval_id", nargs="?", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _bootstrap_registry()
    mgr = TaskManager(args.db)

    if args.cmd == "create":
        steps = make_steps(args.steps, args.fail_step)
        task = mgr.create_task(args.request, steps)
        print(f"created {task.task_id} ({args.steps} steps) request={args.request!r}")
        return 0

    if args.cmd == "list":
        tasks = mgr.list_tasks()
        if not tasks:
            print("no tasks")
        for task in tasks:
            print(f"  {task.task_id}  {task.status.value:<16} {task.request!r}")
        return 0

    if args.cmd == "approvals":
        pending = mgr.pending_approvals(args.task_id)
        if not pending:
            print("no pending approvals")
        for a in pending:
            print(
                f"  {a['approval_id']}  task={a['task_id']}  step={a['step_id']}  "
                f"tool={a['tool']}  diff={json.dumps(a['diff_card'], sort_keys=True)}"
            )
        return 0

    task_id = args.task_id or _latest_task(mgr)

    if args.cmd == "approve" or args.cmd == "reject":
        if not args.approval_id:
            print("provide the approval_id (see `approvals`)")
            return 1
        if args.cmd == "approve":
            task = mgr.approve(task_id, args.approval_id)
        else:
            task = mgr.reject(task_id, args.approval_id)
        print(f"[{task.task_id}] {args.cmd}d {args.approval_id} (task now {task.status.value})")
        return 0

    if args.cmd == "run":
        task, outcome = mgr.run_next_step(task_id, default_execute)
        if outcome.step is None:
            print(f"[{task.task_id}] no runnable step (task status={task.status.value})")
            return 0
        print(
            f"[{task.task_id}] step {outcome.step.step_id} tool={outcome.step.tool} "
            f"ok={outcome.ok} error={outcome.error!r}"
        )
        if task.status.value == "waiting_approval":
            print("pending approval — see `approvals <task_id>` then `approve`/`reject`")
        print(f"task status={task.status.value}")
    elif args.cmd == "pause":
        task = mgr.pause(task_id)
        print(f"[{task.task_id}] paused")
    elif args.cmd == "resume":
        task = mgr.resume(task_id)
        print(f"[{task.task_id}] resumed")
    elif args.cmd == "cancel":
        task = mgr.cancel(task_id)
        print(f"[{task.task_id}] cancelled")
    elif args.cmd == "retry":
        task = mgr.get(task_id)
        failed = [s.step_id for s in task.steps if s.status.value == "failed"]
        if not failed:
            print("no failed steps")
            return 0
        mgr.retry_step(task_id, failed[0])
        print(f"[{task.task_id}] re-armed {failed[0]} (same idempotency_key)")
    elif args.cmd == "status":
        task = mgr.get(task_id)
        print(f"task={task.task_id} status={task.status.value} request={task.request!r}")
        for step in task.steps:
            print(
                f"  {step.step_id}  tool={step.tool:<20} status={step.status.value:<12} "
                f"error={step.error or ''}"
            )
    return 0


def _latest_task(mgr: TaskManager) -> str:
    tasks = mgr.list_tasks(limit=1)
    if not tasks:
        print("no tasks — create one first")
        sys.exit(1)
    return tasks[0].task_id


if __name__ == "__main__":
    sys.exit(main())
