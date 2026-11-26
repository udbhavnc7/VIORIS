"""Phase 9.4: local-first LLM failover across free-tier hosted backends.

No network calls here: each backend is either a small fake or is exercised
through a mocked httpx.Client, so the local-first guarantee is asserted without
requiring Ollama or an API key.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from packages.shared.infrastructure import LLMRouter
from services.orchestrator.app.llm_client import (
    FallbackLLMBackend,
    GeminiBackend,
    OllamaBackend,
    OpenAICompatBackend,
    PlannerBackendError,
    default_llm_backend,
    _extract_openai_tool_calls,
    _extract_tool_calls,
)

MESSAGES = [{"role": "user", "content": "what time is it"}]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "system.get_time",
            "description": "read the clock",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


class FakeBackend:
    def __init__(self, result=None, error: str | None = None) -> None:
        self.result = result or {"tool_calls": [{"name": "system.get_time", "arguments": {}}]}
        self.error = error
        self.calls = 0

    def chat_with_tools(self, messages, tools):
        self.calls += 1
        if self.error:
            raise PlannerBackendError(self.error)
        return self.result


class TestDefaultBackend:
    def test_ollama_is_default_even_when_hosted_keys_are_set(self):
        with patch.dict(
            os.environ,
            {
                "VIORIS_LLM_FALLBACKS": "0",
                "GROQ_API_KEY": "g-key",
                "OPENROUTER_API_KEY": "or-key",
                "GEMINI_API_KEY": "gem-key",
            },
            clear=False,
        ):
            assert isinstance(default_llm_backend(), OllamaBackend)

    def test_hosted_fallbacks_require_explicit_opt_in(self):
        with patch.dict(os.environ, {"VIORIS_LLM_FALLBACKS": "1"}, clear=False):
            assert isinstance(default_llm_backend(), FallbackLLMBackend)

    def test_planner_uses_local_default(self):
        from services.orchestrator.app.planner import Planner

        with patch.dict(
            os.environ,
            {"VIORIS_LLM_FALLBACKS": "0", "GROQ_API_KEY": "g-key"},
            clear=False,
        ):
            assert isinstance(Planner().backend, OllamaBackend)


class TestFallbackChain:
    def test_local_ollama_is_tried_first_and_wins(self):
        ollama = FakeBackend()
        groq = FakeBackend()
        backend = FallbackLLMBackend(
            ollama=ollama,
            fallbacks=[("groq", groq)],
        )
        assert backend.chat_with_tools(MESSAGES, TOOLS) == ollama.result
        assert ollama.calls == 1
        assert groq.calls == 0

    def test_rate_limited_local_fails_over_to_free_tier(self):
        ollama = FakeBackend(error="Ollama request failed: 429 rate limited")
        groq = FakeBackend()
        backend = FallbackLLMBackend(ollama=ollama, fallbacks=[("groq", groq)])
        assert backend.chat_with_tools(MESSAGES, TOOLS) == groq.result
        assert ollama.calls == 1
        assert groq.calls == 1

    def test_fallback_order_is_preserved(self):
        chain = []
        backends = []
        for name in ("groq", "openrouter", "gemini"):
            def make(name):
                def call(_messages, _tools, name=name):
                    chain.append(name)
                    raise PlannerBackendError("429 rate limit")
                return MagicMock(chat_with_tools=call)
            backends.append((name, make(name)))
        backend = FallbackLLMBackend(
            ollama=FakeBackend(error="down"), fallbacks=backends
        )
        with pytest.raises(PlannerBackendError) as exc:
            backend.chat_with_tools(MESSAGES, TOOLS)
        assert chain == ["groq", "openrouter", "gemini"]
        for name in ("ollama", "groq", "openrouter", "gemini"):
            assert name in str(exc.value)

    def test_provider_availability_is_reported_to_router(self):
        router = LLMRouter()
        ollama = FakeBackend(error="unreachable")
        groq = FakeBackend()
        backend = FallbackLLMBackend(
            ollama=ollama, router=router, fallbacks=[("groq", groq)]
        )
        backend.chat_with_tools(MESSAGES, TOOLS)
        assert router._provider_status["ollama"] is False
        assert router._provider_status["groq"] is True

    def test_all_backends_down_raises_instead_of_silent_noop(self):
        backend = FallbackLLMBackend(
            ollama=FakeBackend(error="down"),
            fallbacks=[("groq", FakeBackend(error="429"))],
        )
        with pytest.raises(PlannerBackendError):
            backend.chat_with_tools(MESSAGES, TOOLS)

    def test_env_keys_build_groq_openrouter_gemini_chain(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "g-key",
                "OPENROUTER_API_KEY": "or-key",
                "GEMINI_API_KEY": "gem-key",
            },
            clear=False,
        ):
            chain = FallbackLLMBackend(ollama=FakeBackend())._build_chain()
        assert [name for name, _ in chain] == ["ollama", "groq", "openrouter", "gemini"]
        assert isinstance(chain[1][1], OpenAICompatBackend)
        assert "groq" in chain[1][1].base_url
        assert "openrouter" in chain[2][1].base_url
        assert isinstance(chain[3][1], GeminiBackend)

    def test_no_env_keys_means_local_only(self):
        with patch.dict(
            os.environ,
            {"GROQ_API_KEY": "", "OPENROUTER_API_KEY": "", "GEMINI_API_KEY": ""},
            clear=False,
        ):
            chain = FallbackLLMBackend(ollama=FakeBackend())._build_chain()
        assert [name for name, _ in chain] == ["ollama"]


class TestBackendToolCallMapping:
    def test_openai_compat_maps_tool_calls_and_parses_string_arguments(self):
        data = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "system.set_reminder",
                                    "arguments": '{"reminder": "call mom", "at": "3pm"}',
                                }
                            }
                        ]
                    }
                }
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        with patch("services.orchestrator.app.llm_client.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = resp
            result = OpenAICompatBackend(
                provider="groq", api_key="k", base_url="https://api.groq.com/openai/v1", model="m"
            ).chat_with_tools(MESSAGES, TOOLS)
        assert result["tool_calls"] == [
            {"name": "system.set_reminder", "arguments": {"reminder": "call mom", "at": "3pm"}}
        ]

    def test_openai_compat_without_key_raises(self):
        with pytest.raises(PlannerBackendError, match="not configured"):
            OpenAICompatBackend(
                provider="groq", api_key="", base_url="https://x", model="m"
            ).chat_with_tools(MESSAGES, TOOLS)

    def test_openai_compat_http_error_becomes_backend_error(self):
        with patch("services.orchestrator.app.llm_client.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError("offline")
            with pytest.raises(PlannerBackendError, match="groq request failed"):
                OpenAICompatBackend(
                    provider="groq", api_key="k", base_url="https://x", model="m"
                ).chat_with_tools(MESSAGES, TOOLS)

    def test_gemini_maps_function_calls_and_system_instruction(self):
        data = {
            "candidates": [
                {"content": {"parts": [{"functionCall": {"name": "system.get_time", "args": {}}}]}}
            ]
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        with patch("services.orchestrator.app.llm_client.httpx.Client") as client:
            post = client.return_value.__enter__.return_value.post
            post.return_value = resp
            result = GeminiBackend(api_key="k").chat_with_tools(
                [{"role": "system", "content": "be careful"}, *MESSAGES], TOOLS
            )
        assert result["tool_calls"] == [{"name": "system.get_time", "arguments": {}}]
        payload = post.call_args.kwargs["json"]
        assert payload["systemInstruction"]["parts"][0]["text"] == "be careful"
        assert payload["tools"][0]["functionDeclarations"][0]["name"] == "system.get_time"

    def test_ollama_string_arguments_are_parsed(self):
        data = {
            "message": {
                "tool_calls": [
                    {"function": {"name": "system.get_time", "arguments": "{}"}}
                ]
            }
        }
        assert _extract_tool_calls(data, "qwen") == [
            {"name": "system.get_time", "arguments": {}}
        ]

    def test_malformed_provider_json_fails_over_instead_of_aborting(self):
        resp = MagicMock()
        resp.json.side_effect = ValueError("not json")
        bad = OpenAICompatBackend(
            provider="groq", api_key="k", base_url="https://x", model="m"
        )
        with patch("services.orchestrator.app.llm_client.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = resp
            chain = FallbackLLMBackend(ollama=FakeBackend(error="down"), fallbacks=[("groq", bad)])
            with pytest.raises(PlannerBackendError, match="groq returned invalid JSON"):
                chain.chat_with_tools(MESSAGES, TOOLS)

    def test_json_content_fallback_still_works(self):
        content = '[{"name": "system.get_time", "arguments": {}}]'
        assert _extract_openai_tool_calls({"content": content}) == [
            {"name": "system.get_time", "arguments": {}}
        ]
