"""
Computer agent CLI (Phase 3, Prompt 3.1).

Drive the four tools from the terminal with injectable backends passed through
the agent's defaults:

    python -m agents.computer.app.cli windows
    python -m agents.computer.app.cli screenshot
    python -m agents.computer.app.cli screen-text
    python -m agents.computer.app.cli open <app>
"""

from __future__ import annotations

import argparse
import json
import sys

from packages.shared.permission_engine import register_phase3_tools

from .agent import ComputerAgent


def main(argv: list[str] | None = None) -> int:
    register_phase3_tools()
    parser = argparse.ArgumentParser(prog="vioris-computer")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("windows", help="list open windows")
    sub.add_parser("screenshot", help="capture a screenshot")
    sub.add_parser("screen-text", help="OCR the screen")
    p = sub.add_parser("open", help="open an app by name")
    p.add_argument("name")
    args = parser.parse_args(argv)

    agent = ComputerAgent()
    if args.cmd == "windows":
        out = agent.list_windows()
    elif args.cmd == "screenshot":
        out = agent.capture_screenshot()
    elif args.cmd == "screen-text":
        out = agent.read_screen_text()
    else:
        out = agent.open_app(args.name)

    print(json.dumps(out.detail, default=str, indent=2))
    print(f"ok={out.ok} verified={out.verified} note={out.note!r}")
    if not out.ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())