"""Integration tests — full pipeline (agent → dialogue → execute → respond)."""

import asyncio
import pytest
from apps.desktop_agent.agent import DesktopAgent, AgentState, AgentEvent
from packages.shared.config import ViorisConfig
from packages.shared.contacts import Contact, ContactBook


class TestDesktopAgent:
    def test_init(self):
        agent = DesktopAgent()
        assert agent.state == AgentState.IDLE
        assert agent._commands_processed == 0

    def test_config_defaults(self):
        config = ViorisConfig()
        assert config.llm_provider == "ollama"
        assert config.wake_word_enabled

    def test_config_from_env(self, monkeypatch):
        monkeypatch.setenv("VIORIS_LLM_PROVIDER", "groq")
        monkeypatch.setenv("VIORIS_LLM_MODEL", "mixtral")
        config = ViorisConfig.from_env()
        assert config.llm_provider == "groq"
        assert config.llm_model == "mixtral"

    def test_config_validate(self):
        config = ViorisConfig()
        valid, errors = config.validate()
        assert valid

    def test_config_validate_bad_port(self):
        config = ViorisConfig(ws_port=99999)
        valid, errors = config.validate()
        assert not valid
        assert "WS_PORT" in errors[0]

    @pytest.mark.asyncio
    async def test_process_utterance(self):
        agent = DesktopAgent()
        result = await agent.process_utterance("check email")
        assert "response" in result
        assert "sub_intents" in result
        assert agent._commands_processed == 1

    @pytest.mark.asyncio
    async def test_process_empty_utterance(self):
        agent = DesktopAgent()
        result = await agent.process_utterance("")
        assert "response" in result

    @pytest.mark.asyncio
    async def test_process_compound_command(self):
        agent = DesktopAgent()
        result = await agent.process_utterance("check email and turn on lights")
        assert len(result["sub_intents"]) >= 1

    @pytest.mark.asyncio
    async def test_handle_call(self):
        agent = DesktopAgent()
        result = await agent.handle_call("Disha")
        assert result["status"] == "ringing"
        assert result["caller"] == "Disha"
        assert agent.state == AgentState.LISTENING

    @pytest.mark.asyncio
    async def test_end_call(self):
        agent = DesktopAgent()
        await agent.handle_call("Disha")
        result = await agent.end_call()
        assert result["status"] == "ended"
        assert agent.state == AgentState.IDLE

    @pytest.mark.asyncio
    async def test_get_digest(self):
        agent = DesktopAgent()
        result = await agent.get_digest()
        assert result["spoken"] == "Nothing new."

    @pytest.mark.asyncio
    async def test_get_digest_with_items(self):
        agent = DesktopAgent()
        items = [{"item_type": "email", "summary": "Meeting at 3", "source": "Boss"}]
        result = await agent.get_digest(items)
        assert "Meeting at 3" in result["spoken"]

    def test_get_status(self):
        agent = DesktopAgent()
        status = agent.get_status()
        assert status["state"] == "idle"
        assert "uptime_seconds" in status
        assert "commands_processed" in status

    def test_preferences(self):
        agent = DesktopAgent()
        agent.set_preference("theme", "dark")
        prefs = agent.get_preferences()
        assert prefs["theme"]["value"] == "dark"

    def test_contacts(self):
        agent = DesktopAgent()
        agent.add_contact("Disha", phone="123", email="d@test.com")
        contacts = agent.get_contacts()
        assert len(contacts) == 1
        assert contacts[0]["name"] == "Disha"

    @pytest.mark.asyncio
    async def test_event_handlers(self):
        agent = DesktopAgent()
        received = []
        agent.on("test_event", lambda e: received.append(e))
        await agent._emit(AgentEvent("test_event", {"key": "value"}))
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_multiple_commands(self):
        agent = DesktopAgent()
        await agent.process_utterance("check email")
        await agent.process_utterance("turn on lights")
        await agent.process_utterance("take screenshot")
        assert agent._commands_processed == 3

    @pytest.mark.asyncio
    async def test_smart_home_integration(self):
        agent = DesktopAgent()
        result = await agent.process_utterance("turn on the lights")
        assert "response" in result

    @pytest.mark.asyncio
    async def test_error_handling(self):
        agent = DesktopAgent()
        result = await agent.process_utterance("")
        assert "response" in result

    def test_responder_integration(self):
        agent = DesktopAgent()
        agent.set_preference("response_style", "verbose")
        greeting = agent.responder.get_greeting()
        assert isinstance(greeting, str)

    @pytest.mark.asyncio
    async def test_pattern_learning(self):
        agent = DesktopAgent()
        await agent.process_utterance("hello")
        suggestions = agent.patterns.get_suggestions("hello")
        assert len(suggestions) >= 1
