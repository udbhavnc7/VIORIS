"""
Task-runner tool executor (Phase 5, Prompt 5.3).

Replaces the Phase 2 stand-in with a REAL router: execute/critical steps that
the user approved are dispatched to the correct local agent daemon over HTTP,
then verified. The LLM never runs tools — the engine calls `execute(step)`,
which routes by tool name to the agent that owns it.

Current routes:
  - computer.* and system.open_app  -> computer agent daemon (VIORUS_COMPUTER_URL)
  - anything else                   -> refused (never executed silently)
"""

from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

COMPUTER_URL = os.getenv("VIORUS_COMPUTER_URL", "http://127.0.0.1:8430")


def make_executor(computer_url: str = COMPUTER_URL):
    """Return an `execute(step)` closure wired to the local agent daemons."""

    def _dispatch(step):
        tool = step.tool
        args = (step.result or {}).get("args") or {}

        if tool in ("computer.start_remote_session", "computer.remote_input"):
            return _call_computer(tool, args, computer_url)
        if tool in ("computer.capture_screenshot", "computer.list_windows", "system.open_app"):
            return _call_computer(tool, args, computer_url)
        raise RuntimeError(f"no executor for tool {tool!r} — refused, not guessed")

    return _dispatch


def _call_computer(tool: str, args: dict, base: str) -> dict:
    """Call the computer agent daemon and return a verified result dict."""
    try:
        timeout = float(args.get("timeout") or 45)
    except (TypeError, ValueError):
        timeout = 45.0
    started = time.time()
    if tool == "computer.start_remote_session":
        url = f"{base}/remote/session"
        payload = {"device_id": args["device_id"], "timeout_minutes": args.get("timeout_minutes", 5)}
    elif tool == "computer.remote_input":
        url = f"{base}/remote/input"
        payload = {
            "session_id": args["session_id"],
            "device_id": args["device_id"],
            "action": args["action"],
            "text": args.get("text"),
            "x": args.get("x"),
            "y": args.get("y"),
        }
    elif tool == "computer.capture_screenshot":
        url = f"{base}/screenshot"
        payload = None
    elif tool == "computer.list_windows":
        url = f"{base}/windows"
        payload = None
    elif tool == "system.open_app":
        url = f"{base}/open-app"
        payload = {"name": args.get("name") or args.get("app")}
    else:  # pragma: no cover — guarded by _dispatch
        raise RuntimeError(f"unrouted tool {tool}")

    try:
        with httpx.Client(timeout=timeout) as client:
            if payload is not None:
                resp = client.post(url, json=payload)
            else:
                resp = client.get(url)
    except httpx.RequestError as exc:  # noqa: BLE001
        raise RuntimeError(f"agent daemon unreachable for {tool}: {exc}") from exc

    body = _parse(resp)
    latency_ms = int((time.time() - started) * 1000)
    if resp.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else str(body)
        raise RuntimeError(f"{tool} rejected: {detail}")
    return {"ok": True, "tool": tool, "latency_ms": latency_ms, "result": body}


def _parse(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"raw": resp.text[:500]}