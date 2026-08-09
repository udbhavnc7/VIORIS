"""
Vioris desktop agent — Phase 1.

Bootstraps the permission registry (register Phase 1 tools, freeze), then
either:
    * default: prints the registered tool table (smoke check)
    * --listen: runs the voice loop (wake-word -> STT -> command -> TTS)
    * --web: serves the local browser console at http://127.0.0.1:8000

Run from the repo root:
    python -m apps.desktop_agent.app.main
    python -m apps.desktop_agent.app.main --listen
    python -m apps.desktop_agent.app.main --web
"""

from __future__ import annotations

import argparse
import logging
import sys

from packages.shared.permission_engine import (
    PermissionEngine,
    register_phase1_tools,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def bootstrap_registry() -> None:
    """Register all Phase 1 tools and freeze the registry.

    The registry is the single source of truth for risk classification. It is
    frozen before any command handling exists so no code path can add a tool
    at runtime.
    """
    register_phase1_tools()
    PermissionEngine.freeze()
    logger.info("Permission registry frozen with %d tools.", len(PermissionEngine.list_tools()))


def print_registry() -> None:
    """Phase 1 smoke check: show the frozen tool table."""
    for reg in sorted(PermissionEngine.list_tools(), key=lambda r: r.tool_name):
        result = PermissionEngine.classify(reg.tool_name)
        print(
            f"  {result.tool_name:<22} tier={result.tier.value:<9} "
            f"confirmation={str(result.confirmation_required):<5}"
        )


def build_voice_loop():
    """Build the Phase 1 voice pipeline from the environment config."""
    from .config import AgentConfig
    from .executor import Executor
    from .services.audio_source import MicrophoneSource
    from .services.state_machine import AgentStateMachine
    from .services.stt import WhisperSTT
    from .services.tts import PiperTTS, find_piper_voice
    from .services.voice_loop import VoiceLoop
    from .services.wake_detector import ClapDetector, WakeWordDetector
    from .store import TaskStore

    cfg = AgentConfig()

    voice = find_piper_voice(cfg.tts_model, [cfg.models_dir])
    tts = PiperTTS(voice)
    stt = WhisperSTT(cfg.stt_model, cfg.stt_device, cfg.stt_compute_type)
    source = MicrophoneSource(cfg.sample_rate, cfg.chunk_samples)
    wake = WakeWordDetector(cfg.wake_word_model, cfg.wake_threshold)
    clap = ClapDetector(
        required=cfg.claps_required,
        window_seconds=cfg.clap_window_seconds,
        energy_threshold=0.2,
    )
    store = TaskStore(cfg.db_path)
    state = AgentStateMachine()
    return VoiceLoop(state, wake, clap, tts, stt, source, cfg, executor=Executor(), store=store)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vioris-agent")
    parser.add_argument(
        "--listen",
        action="store_true",
        help="run the voice loop (wake word -> reply) instead of printing the registry",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="serve the local browser console at http://127.0.0.1:8000",
    )
    args = parser.parse_args(argv)

    bootstrap_registry()

    if args.web:
        return _run_web()
    if args.listen:
        loop = build_voice_loop()
        print("Vioris desktop agent — listening (Ctrl+C to stop)")
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
        return 0

    print("Vioris desktop agent — Phase 1 skeleton")
    print("Registered tools:")
    print_registry()
    return 0


def _run_web(host: str = "127.0.0.1", port: int = 8000) -> int:
    """Serve the local web console. Loopback only — not exposed to Tailscale
    until Phase 5 hardens the remote access surface."""
    import uvicorn

    from .web import app

    print(f"Vioris console: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
