"""
Memory Service CLI (Phase 4, Prompt 4.1).

    python -m services.memory_service.app.cli add --category people --content "..." --source "user said ..."
    python -m services.memory_service.app.cli add --category sensitive --content "..." --source "..." --opt-in
    python -m services.memory_service.app.cli list [--category people]
    python -m services.memory_service.app.cli correct <memory_id> --content "..."
    python -m services.memory_service.app.cli delete <memory_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from packages.shared.schemas import MemoryCategory

from .store import MemoryStore, SensitiveOptInRequiredError

DEFAULT_DB = Path("vioris_data/memory.db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vioris-memory")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="store a memory")
    p.add_argument("--category", required=True, choices=[c.value for c in MemoryCategory])
    p.add_argument("--content", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--confidence", type=float, default=1.0)
    p.add_argument("--opt-in", dest="opt_in", action="store_true", help="explicit user opt-in (required for sensitive)")

    p = sub.add_parser("list", help="list memories")
    p.add_argument("--category", choices=[c.value for c in MemoryCategory])

    p = sub.add_parser("correct", help="correct a memory")
    p.add_argument("memory_id")
    p.add_argument("--content", required=True)

    p = sub.add_parser("delete", help="delete a memory by id")
    p.add_argument("memory_id")

    args = parser.parse_args(argv)
    store = MemoryStore(args.db)

    try:
        if args.cmd == "add":
            res = store.store_and_user_opt_in(
                MemoryCategory(args.category),
                args.content,
                args.source,
                args.confidence,
                user_opted_in=args.opt_in,
            )
            if not res.ok:
                print(f"ERROR: {res.error}")
                return 1
            print(json.dumps(_mem_json(res.memory), indent=2))
            return 0

        if args.cmd == "list":
            cat = MemoryCategory(args.category) if args.category else None
            for m in store.list(cat):
                print(json.dumps(_mem_json(m)))
            return 0

        if args.cmd == "correct":
            res = store.correct(args.memory_id, args.content)
            if not res.ok:
                print(f"ERROR: {res.error}")
                return 1
            print(json.dumps(_mem_json(res.memory), indent=2))
            return 0

        if args.cmd == "delete":
            res = store.delete(args.memory_id)
            print(f"deleted={res.detail.get('deleted')} memory_id={args.memory_id}")
            return 0
    except SensitiveOptInRequiredError as exc:
        print(f"ERROR: {exc}")
        return 1

    return 1


def _mem_json(mem) -> dict:
    return {
        "memory_id": mem.memory_id,
        "category": mem.category.value,
        "content": mem.content,
        "source": mem.source,
        "confidence": mem.confidence,
        "user_opted_in": mem.user_opted_in,
        "created_at": mem.created_at.isoformat(),
        "updated_at": mem.updated_at.isoformat(),
    }


if __name__ == "__main__":
    sys.exit(main())