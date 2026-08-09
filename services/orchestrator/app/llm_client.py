"""
Injectable LLM client for the planner (Phase 2, Prompt 2.2).

Default: local Ollama over its HTTP API (cost $0 — the software need never
touch a paid endpoint). Injectable so tests use a deterministic fake.

The planner depends on a narrow contract: `chat_with_tools(messages, tools)`
returns a dict shaped like:

    {"tool_calls": [{"name": "...", "arguments": {...}}, ...]}

Every backend (Ollama, a future free-tier host) maps into that shape.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PlannerBackendError(Exception):
    """The configured LLM backend failed in a way that prevents planning."""


class OllamaBackend:
    """Local LLM via Ollama /api/chat with function tools (JSON output)."""

    def __init__(
        self, model: str | None = None, base_url: str | None = None, timeout: float = 120.0
    ) -> None:
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:8b")
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip(
            "/"
        )
        self.timeout = timeout

    def chat_with_tools(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise PlannerBackendError(f"Ollama request failed: {exc}") from exc

        tool_calls = _extract_tool_calls(data, self.model)
        return {"tool_calls": tool_calls}


def _extract_tool_calls(data: dict, model: str) -> list[dict]:
    """Ollama returned tool calls are message.tool_calls; some models only emit
    JSON in the message text — tolerate both so the planner still works on a
    weak local model. Never trust the model's risk labels: it is not asked for
    them (see planner.py)."""
    message = (data.get("message") or {}).get("tool_calls") or []
    calls = []
    for call in message:
        fn = call.get("function", {})
        name = fn.get("name")
        if not name:
            continue
        arguments = fn.get("arguments", {})
        calls.append({"name": name, "arguments": arguments or {}})
    if calls:
        return calls
    # Fallback: some local models reply with a JSON array in message.content.
    content = (data.get("message") or {}).get("content", "")
    parsed = _try_parse_json(content)
    if isinstance(parsed, list):
        return [c for c in parsed if isinstance(c, dict) and c.get("name")]
    return []


def _try_parse_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
