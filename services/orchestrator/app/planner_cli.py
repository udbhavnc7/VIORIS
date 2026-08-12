"""
Planner CLI (Phase 2, Prompt 2.2).

Shows the proposed plan as JSON WITHOUT executing anything:

    python -m services.orchestrator.app.planner_cli "open vs code then tell me the time"

Everything is inert by design — the plan is printed, never run. Risk levels are
pulled from the frozen registry, unregistered tools are listed as blocked.
"""

from __future__ import annotations

import argparse
import json
import sys

from packages.shared.permission_engine import (
    register_phase1_tools,
    register_phase6_connector_tools,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vioris-planner")
    parser.add_argument("request", help='transcript, e.g. "open vs code and tell me the time"')
    parser.add_argument(
        "--model", default=None, help="Ollama model (defaults to OLLAMA_MODEL or qwen3:8b)"
    )
    parser.add_argument(
        "--base-url", default=None, help="Ollama base URL (default localhost:11434)"
    )
    args = parser.parse_args(argv)

    from packages.shared.permission_engine import PermissionEngine

    PermissionEngine.reset()
    register_phase1_tools()
    register_phase6_connector_tools()  # Phase 6 connector fleet (observe/execute/prepare)
    PermissionEngine.freeze()

    from .llm_client import OllamaBackend
    from .planner import Planner

    backend = OllamaBackend(model=args.model, base_url=args.base_url)
    plan = Planner(backend=backend).plan(args.request)
    print(json.dumps(plan.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
