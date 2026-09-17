"""Tests for Phase 11 (Smart Home) + Phase 12 (Multi-Agent)."""

import asyncio
import time

import pytest
from packages.shared.smart_home import (
    EntityState,
    HomeAssistantConnector,
    ServiceCall,
)
from packages.shared.multi_agent import (
    AgentOrchestrator,
    AgentResult,
    AgentRole,
    AgentStatus,
    AgentTask,
    SubAgent,
)


# --- Phase 11: Smart Home Tests ---


class TestEntityState:
    def test_is_on(self):
        state = EntityState(entity_id="light.living", state="on")
        assert state.is_on()

    def test_is_on_playing(self):
        state = EntityState(entity_id="media_player.tv", state="playing")
        assert state.is_on()

    def test_is_off(self):
        state = EntityState(entity_id="light.living", state="off")
        assert state.is_off()

    def test_is_off_closed(self):
        state = EntityState(entity_id="cover.garage", state="closed")
        assert state.is_off()

    def test_get_attribute(self):
        state = EntityState(entity_id="light.living", state="on", attributes={"brightness": 180})
        assert state.get_attribute("brightness") == 180
        assert state.get_attribute("color", "white") == "white"

    def test_to_dict(self):
        state = EntityState(entity_id="light.living", state="on")
        d = state.to_dict()
        assert d["entity_id"] == "light.living"
        assert d["state"] == "on"


class TestHomeAssistantConnector:
    def test_init(self):
        conn = HomeAssistantConnector()
        assert not conn.is_connected()

    def test_connect_no_token(self):
        conn = HomeAssistantConnector(token="")
        success, msg = conn.connect()
        assert not success
        assert "No token" in msg

    def test_connect_with_token(self):
        conn = HomeAssistantConnector(token="test_token")
        success, msg = conn.connect()
        assert success
        assert conn.is_connected()

    def test_set_and_get_state(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        conn.set_state("light.living", "on", {"brightness": 200})
        state = conn.get_state("light.living")
        assert state is not None
        assert state.is_on()
        assert state.get_attribute("brightness") == 200

    def test_get_states_by_domain(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        conn.set_state("light.living", "on")
        conn.set_state("light.bedroom", "off")
        conn.set_state("switch.fan", "on")
        lights = conn.get_states("light")
        assert len(lights) == 2

    def test_call_service_light_on(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        call = ServiceCall(domain="light", service="turn_on", entity_id="light.living", data={"brightness": 200})
        success, msg = conn.call_service(call)
        assert success
        state = conn.get_state("light.living")
        assert state.is_on()

    def test_call_service_light_toggle(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        conn.set_state("light.living", "off")
        call = ServiceCall(domain="light", service="toggle", entity_id="light.living")
        conn.call_service(call)
        assert conn.get_state("light.living").is_on()

    def test_call_service_switch(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        call = ServiceCall(domain="switch", service="turn_on", entity_id="switch.fan")
        conn.call_service(call)
        assert conn.get_state("switch.fan").is_on()

    def test_call_service_climate(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        call = ServiceCall(domain="climate", service="set_temperature", entity_id="climate.thermostat", data={"temperature": 24})
        conn.call_service(call)
        state = conn.get_state("climate.thermostat")
        assert state.get_attribute("temperature") == 24

    def test_call_service_lock(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        call = ServiceCall(domain="lock", service="lock", entity_id="lock.front_door")
        conn.call_service(call)
        assert conn.get_state("lock.front_door").state == "locked"

    def test_call_service_cover(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        call = ServiceCall(domain="cover", service="open_cover", entity_id="cover.garage")
        conn.call_service(call)
        assert conn.get_state("cover.garage").state == "open"

    def test_call_service_not_connected(self):
        conn = HomeAssistantConnector()
        call = ServiceCall(domain="light", service="turn_on", entity_id="light.living")
        success, msg = conn.call_service(call)
        assert not success

    def test_get_light_state(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        conn.set_state("light.living", "on", {"brightness": 150})
        result = conn.get_light_state("light.living")
        assert result["on"]
        assert result["brightness"] == 150

    def test_get_climate_state(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        conn.set_state("climate.ac", "cool", {"temperature": 20})
        result = conn.get_climate_state("climate.ac")
        assert result["mode"] == "cool"
        assert result["temperature"] == 20

    def test_get_lock_state(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        conn.set_state("lock.door", "locked")
        result = conn.get_lock_state("lock.door")
        assert result["locked"]

    def test_list_devices(self):
        conn = HomeAssistantConnector(token="test")
        conn.connect()
        conn.set_state("light.living", "on")
        conn.set_state("light.bedroom", "off")
        devices = conn.list_devices("light")
        assert len(devices) == 2


# --- Phase 12: Multi-Agent Tests ---


class TestSubAgent:
    def test_init(self):
        agent = SubAgent(role=AgentRole.EMAIL)
        assert agent.role == AgentRole.EMAIL
        assert agent.status == AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_execute_with_handler(self):
        async def handler(task):
            return {"emails": 5}

        agent = SubAgent(role=AgentRole.EMAIL, handler=handler)
        task = AgentTask(task_id="t1", agent_role=AgentRole.EMAIL, description="check email")
        result = await agent.execute(task)
        assert result.success
        assert result.data == {"emails": 5}
        assert agent._tasks_completed == 1

    @pytest.mark.asyncio
    async def test_execute_without_handler(self):
        agent = SubAgent(role=AgentRole.EMAIL)
        task = AgentTask(task_id="t1", agent_role=AgentRole.EMAIL, description="check email")
        result = await agent.execute(task)
        assert result.success
        assert "No handler" in str(result.data)

    @pytest.mark.asyncio
    async def test_execute_error(self):
        async def handler(task):
            raise ValueError("Connection failed")

        agent = SubAgent(role=AgentRole.EMAIL, handler=handler)
        task = AgentTask(task_id="t1", agent_role=AgentRole.EMAIL, description="check email")
        result = await agent.execute(task)
        assert not result.success
        assert "Connection failed" in result.error
        assert agent._errors == 1

    def test_stats(self):
        agent = SubAgent(role=AgentRole.EMAIL)
        agent._tasks_completed = 10
        agent._errors = 2
        stats = agent.stats()
        assert stats["tasks_completed"] == 10
        assert stats["errors"] == 2


class TestAgentTask:
    def test_to_dict(self):
        task = AgentTask(task_id="t1", agent_role=AgentRole.EMAIL, description="check email")
        d = task.to_dict()
        assert d["task_id"] == "t1"
        assert d["agent_role"] == "email"


class TestAgentResult:
    def test_to_dict(self):
        result = AgentResult(task_id="t1", success=True, data={"count": 5}, agent_role="email", duration_ms=100)
        d = result.to_dict()
        assert d["success"]
        assert d["data"]["count"] == 5


class TestAgentOrchestrator:
    def test_init(self):
        orch = AgentOrchestrator()
        assert orch.get_history() == []

    def test_register_agent(self):
        orch = AgentOrchestrator()
        orch.register_agent(AgentRole.EMAIL)
        assert orch.get_agent(AgentRole.EMAIL) is not None

    def test_create_task(self):
        orch = AgentOrchestrator()
        task = orch.create_task(AgentRole.EMAIL, "check email")
        assert task.task_id
        assert task.agent_role == AgentRole.EMAIL

    @pytest.mark.asyncio
    async def test_execute_task(self):
        orch = AgentOrchestrator()
        orch.register_agent(AgentRole.EMAIL)
        task = orch.create_task(AgentRole.EMAIL, "check email")
        result = await orch.execute_task(task)
        assert result.success
        assert len(orch.get_history()) == 1

    @pytest.mark.asyncio
    async def test_execute_task_no_agent(self):
        orch = AgentOrchestrator()
        task = AgentTask(task_id="t1", agent_role=AgentRole.EMAIL, description="check email")
        result = await orch.execute_task(task)
        assert not result.success
        assert "No agent" in result.error

    @pytest.mark.asyncio
    async def test_execute_parallel(self):
        orch = AgentOrchestrator()
        orch.register_agent(AgentRole.EMAIL)
        orch.register_agent(AgentRole.BROWSER)

        tasks = [
            orch.create_task(AgentRole.EMAIL, "check email"),
            orch.create_task(AgentRole.BROWSER, "open website"),
        ]
        results = await orch.execute_parallel(tasks)
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_decompose_request_email(self):
        orch = AgentOrchestrator()
        tasks = orch.decompose_request("check my email")
        assert any(t.agent_role == AgentRole.EMAIL for t in tasks)

    def test_decompose_request_browser(self):
        orch = AgentOrchestrator()
        tasks = orch.decompose_request("open google.com")
        assert any(t.agent_role == AgentRole.BROWSER for t in tasks)

    def test_decompose_request_smart_home(self):
        orch = AgentOrchestrator()
        tasks = orch.decompose_request("turn on the lights")
        assert any(t.agent_role == AgentRole.SMART_HOME for t in tasks)

    def test_decompose_request_calendar(self):
        orch = AgentOrchestrator()
        tasks = orch.decompose_request("what meetings do I have")
        assert any(t.agent_role == AgentRole.CALENDAR for t in tasks)

    def test_decompose_request_computer(self):
        orch = AgentOrchestrator()
        tasks = orch.decompose_request("take a screenshot")
        assert any(t.agent_role == AgentRole.COMPUTER for t in tasks)

    def test_decompose_request_unknown(self):
        orch = AgentOrchestrator()
        tasks = orch.decompose_request("random question about weather")
        assert any(t.agent_role == AgentRole.ORCHESTRATOR for t in tasks)

    def test_get_stats(self):
        orch = AgentOrchestrator()
        orch.register_agent(AgentRole.EMAIL)
        stats = orch.get_stats()
        assert "email" in stats

    def test_clear_history(self):
        orch = AgentOrchestrator()
        orch.register_agent(AgentRole.EMAIL)
        task = orch.create_task(AgentRole.EMAIL, "test")
        orch.get_history()
        orch.clear_history()
        assert len(orch.get_history()) == 0
