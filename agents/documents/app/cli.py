"""
Documents agent CLI (Phase 3, Prompt 3.3).

    python -m agents.documents.app.cli search <query>
    python -m agents.documents.app.cli read <path>
    python -m agents.documents.app.cli move <source> <destination>
    python -m agents.documents.app.cli rename <path> <new_name>
    python -m agents.documents.app.cli delete <path>
    python -m agents.documents.app.cli run <command> [args...]
"""

from __future__ import annotations

import argparse
import json
import sys

from packages.shared.permission_engine import register_phase3_files_tools

from .agent import DocumentsAgent


def main(argv: list[str] | None = None) -> int:
    register_phase3_files_tools()
    parser = argparse.ArgumentParser(prog="vioris-docs")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("search", help="search files by name query")
    p.add_argument("query")
    p = sub.add_parser("read", help="read a file")
    p.add_argument("path")
    p = sub.add_parser("move", help="move a file")
    p.add_argument("source")
    p.add_argument("destination")
    p = sub.add_parser("rename", help="rename a file")
    p.add_argument("path")
    p.add_argument("new_name")
    p = sub.add_parser("delete", help="delete a file (critical)")
    p.add_argument("path")
    p = sub.add_parser("run", help="run an allow-listed command")
    p.add_argument("command")
    p.add_argument("args", nargs="*")
    args = parser.parse_args(argv)

    agent = DocumentsAgent()
    cmd = args.cmd
    if cmd == "search":
        out = agent.search_files(args.query)
    elif cmd == "read":
        out = agent.read_file(args.path)
    elif cmd == "move":
        out = agent.move(args.source, args.destination)
    elif cmd == "rename":
        out = agent.rename(args.path, args.new_name)
    elif cmd == "delete":
        out = agent.delete(args.path)
    else:
        out = agent.run(args.command, args.args)

    print(json.dumps(out.detail, default=str, indent=2))
    print(f"ok={out.ok} note={out.note!r}")
    if out.blocked:
        print(f"BLOCKED: {out.blocked}")
    return 0 if out.ok else 1


if __name__ == "__main__":
    sys.exit(main())