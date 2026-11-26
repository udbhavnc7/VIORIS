"""
Injectable LLM client for the planner (Phase 2, Prompt 2.2 + Phase 9.4 fallbacks).

Default: local Ollama over its HTTP API (cost $0 — the software need never
touch a paid endpoint). When local Ollama is unreachable or fails, and a free-tier
hosted API key is configured (GROQ_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY),
`FallbackLLMBackend` automatically fails over in priority order using `LLMRouter`.

The planner depends on a narrow contract: `chat_with_tools(messages, tools)`
returns a dict shaped like:

    {"tool_calls": [{"name": "...", "arguments": {...}}, ...]}

Every backend (Ollama, Groq, OpenRouter, Gemini free tier) maps into that shape.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from packages.shared.infrastructure import LLMRouter

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
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise PlannerBackendError(f"Ollama request failed: {exc}") from exc

        tool_calls = _extract_tool_calls(data, self.model)
        return {"tool_calls": tool_calls}


class OpenAICompatBackend:
    """OpenAI-compatible /chat/completions backend (Groq and OpenRouter free tiers)."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 45.0,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat_with_tools(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        if not self.api_key:
            raise PlannerBackendError(f"{self.provider} API key is not configured")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "temperature": 0.1,
        }
        try:
            with httpx.Client(timeout=self.timeout, trust_env=True) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise PlannerBackendError(f"{self.provider} request failed: {exc}") from exc

        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        return {"tool_calls": _extract_openai_tool_calls(message)}


class GeminiBackend:
    """Google Gemini free-tier REST backend (generativelanguage API)."""

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 45.0,
    ) -> None:
        self.api_key = api_key
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat_with_tools(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        if not self.api_key:
            raise PlannerBackendError("Gemini API key is not configured")
        system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
        contents = [
            {"role": "user" if m.get("role") == "user" else "model", "parts": [{"text": m.get("content", "")}]}
            for m in messages
            if m.get("role") != "system"
        ]
        declarations = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters": t["function"].get("parameters", {"type": "object", "properties": {}}),
            }
            for t in tools
            if isinstance(t, dict) and "function" in t
        ]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.1},
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
        try:
            with httpx.Client(timeout=self.timeout, trust_env=True) as client:
                resp = client.post(
                    f"{self.base_url}/models/{self.model}:generateContent",
                    params={"key": self.api_key},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise PlannerBackendError(f"Gemini request failed: {exc}") from exc

        calls: list[dict] = []
        for cand in data.get("candidates") or []:
            parts = ((cand.get("content") or {}).get("parts")) or []
            for part in parts:
                fc = part.get("functionCall")
                if isinstance(fc, dict) and fc.get("name"):
                    calls.append({"name": fc["name"], "arguments": fc.get("args") or {}})
        return {"tool_calls": calls}


class FallbackLLMBackend:
    """Local-first LLM backend with automatic free-tier hosted failover (Phase 9.4).

    Order:
      1. Local Ollama (always tried first, $0)
      2. Groq free tier (if GROQ_API_KEY set)
      3. OpenRouter free tier (if OPENROUTER_API_KEY set)
      4. Gemini free tier (if GEMINI_API_KEY set)
    """

    def __init__(
        self,
        ollama: Any | None = None,
        router: LLMRouter | None = None,
        fallbacks: list[tuple[str, Any]] | None = None,
    ) -> None:
        self.ollama = ollama or OllamaBackend()
        self.router = router or LLMRouter()
        self._explicit_fallbacks = fallbacks

    def _build_chain(self) -> list[tuple[str, Any]]:
        if self._explicit_fallbacks is not None:
            return [("ollama", self.ollama), *self._explicit_fallbacks]

        chain: list[tuple[str, Any]] = [("ollama", self.ollama)]
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        if groq_key:
            chain.append(
                (
                    "groq",
                    OpenAICompatBackend(
                        provider="groq",
                        api_key=groq_key,
                        base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
                        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                    ),
                )
            )
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if openrouter_key:
            chain.append(
                (
                    "openrouter",
                    OpenAICompatBackend(
                        provider="openrouter",
                        api_key=openrouter_key,
                        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                        model=os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                    ),
                )
            )
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        if gemini_key:
            chain.append(("gemini", GeminiBackend(api_key=gemini_key)))
        return chain

    def chat_with_tools(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        errors: list[str] = []
        for provider, backend in self._build_chain():
            try:
                result = backend.chat_with_tools(messages, tools)
                self.router.set_provider_status(provider, True)
                return result
            except PlannerBackendError as exc:
                self.router.set_provider_status(provider, False)
                logger.warning("LLM provider '%s' failed: %s", provider, exc)
                errors.append(f"{provider}: {exc}")
        raise PlannerBackendError("; ".join(errors) or "All LLM backends failed")


def _extract_openai_tool_calls(message: dict) -> list[dict]:
    calls = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            parsed = _try_parse_json(raw_args)
            arguments = parsed if isinstance(parsed, dict) else {}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            arguments = {}
        calls.append({"name": name, "arguments": arguments})
    if calls:
        return calls
    parsed = _try_parse_json(message.get("content", ""))
    if isinstance(parsed, list):
        return [c for c in parsed if isinstance(c, dict) and c.get("name")]
    return []


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
        if isinstance(arguments, str):
            parsed = _try_parse_json(arguments)
            arguments = parsed if isinstance(parsed, dict) else {}
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
