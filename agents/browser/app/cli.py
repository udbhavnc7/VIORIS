"""
Browser agent CLI (Phase 3, Prompt 3.2).

    python -m agents.browser.app.cli navigate <url>
    python -m agents.browser.app.cli page
    python -m agents.browser.app.cli fill <description> <value>
    python -m agents.browser.app.cli click <description>
"""

from __future__ import annotations

import argparse
import json
import sys

from packages.shared.permission_engine import register_phase3_browser_tools

from .agent import BrowserAgent


def main(argv: list[str] | None = None) -> int:
    register_phase3_browser_tools()
    parser = argparse.ArgumentParser(prog="vioris-browser")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("navigate", help="navigate to a URL")
    p.add_argument("url")
    sub.add_parser("page", help="read current page text")
    p = sub.add_parser("fill", help="fill a form field")
    p.add_argument("description")
    p.add_argument("value")
    p = sub.add_parser("click", help="click an element by description")
    p.add_argument("description")
    args = parser.parse_args(argv)

    agent = BrowserAgent()
    if args.cmd == "navigate":
        out = agent.navigate(args.url)
    elif args.cmd == "page":
        out = agent.read_page()
    elif args.cmd == "fill":
        out = agent.fill_field(args.description, args.value)
    else:
        out = agent.click_element(args.description)

    print(json.dumps(out.detail, default=str, indent=2))
    print(f"ok={out.ok} note={out.note!r}")
    if out.blocked:
        print(f"BLOCKED: {out.blocked}")
    return 0 if out.ok else 1


if __name__ == "__main__":
    sys.exit(main())