from apps.desktop_agent.app.services.state_machine import (
    AgentStateMachine,
    StateTransitionError,
)
from packages.shared.schemas import AgentState


class TestLegalTransitions:
    def test_starts_idle(self):
        assert AgentStateMachine().state == AgentState.IDLE

    def test_idle_to_listening(self):
        sm = AgentStateMachine()
        sm.to_listening()
        assert sm.state == AgentState.LISTENING

    def test_thinking_to_speaking(self):
        sm = AgentStateMachine()
        sm.to_thinking()
        sm.to_speaking()
        assert sm.state == AgentState.SPEAKING

    def test_speaking_back_to_idle(self):
        sm = AgentStateMachine()
        sm.to_thinking()
        sm.to_speaking()
        sm.to_idle()
        assert sm.state == AgentState.IDLE


class TestIllegalTransitions:
    def test_idle_to_speaking_blocked(self):
        sm = AgentStateMachine()
        try:
            sm.to_speaking()
        except StateTransitionError:
            pass
        assert sm.state == AgentState.IDLE

    def test_raise_actually_fires(self):
        sm = AgentStateMachine()
        try:
            sm.to_speaking()
            raised = False
        except StateTransitionError:
            raised = True
        assert raised

    def test_stopped_reset_via_idle(self):
        sm = AgentStateMachine()
        sm.request_stop()
        sm.to_idle()
        assert sm.state == AgentState.IDLE


class TestHardInterrupt:
    def test_request_stop_sets_event(self):
        sm = AgentStateMachine()
        sm.request_stop()
        assert sm.interrupt_raised() is True

    def test_stop_moves_state_to_stopped(self):
        sm = AgentStateMachine()
        sm.to_thinking()
        sm.request_stop()
        assert sm.state == AgentState.STOPPED

    def test_acknowledge_clears_event(self):
        sm = AgentStateMachine()
        sm.request_stop()
        sm.acknowledge_stop()
        assert sm.interrupt_raised() is False

    def test_stop_while_idle_stays_stopped_until_cleared(self):
        sm = AgentStateMachine()
        sm.request_stop()
        assert sm.state == AgentState.STOPPED
        sm.clear_stop()
        assert sm.interrupt_raised() is False

    def test_on_change_callback_receives_stopped(self):
        seen = []
        sm = AgentStateMachine(on_change=lambda s: seen.append(s))
        sm.to_thinking()
        sm.request_stop()
        assert AgentState.STOPPED in seen
