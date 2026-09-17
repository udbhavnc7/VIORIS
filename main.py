"""Vioris Desktop Agent — entry point."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.desktop_agent.agent import DesktopAgent
from packages.shared.config import ViorisConfig


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vioris Desktop Agent")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument("--port", type=int, default=None, help="API port")
    parser.add_argument("--no-wake-word", action="store_true", help="Disable wake word")
    parser.add_argument("--demo", action="store_true", help="Run in demo mode (text input)")
    return parser


async def run_agent(config: ViorisConfig, demo: bool = False):
    agent = DesktopAgent(config)
    await agent.start()

    if demo:
        print("Vioris Demo Mode — type 'quit' to exit")
        print("=" * 50)
        while True:
            try:
                utterance = input("\nYou: ").strip()
                if utterance.lower() in ("quit", "exit", "stop"):
                    break
                if not utterance:
                    continue
                result = await agent.process_utterance(utterance)
                print(f"\nVioris: {result.get('response', 'No response')}")
                if result.get("requires_approval"):
                    print("  [Approval required for this action]")
            except (KeyboardInterrupt, EOFError):
                break
    else:
        print("Vioris Agent running — press Ctrl+C to stop")
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass

    await agent.stop()
    print("Vioris stopped.")


def main():
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.log_level)

    config = ViorisConfig.from_env()
    if args.port:
        config.api_port = args.port
    if args.no_wake_word:
        config.wake_word_enabled = False

    valid, errors = config.validate()
    if not valid:
        for err in errors:
            print(f"Config error: {err}")
        sys.exit(1)

    asyncio.run(run_agent(config, demo=args.demo))


if __name__ == "__main__":
    main()
