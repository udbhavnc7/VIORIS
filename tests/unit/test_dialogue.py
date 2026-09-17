"""Tests for Phase 8.6 — Dialogue Orchestrator."""

import numpy as np
import pytest

from packages.shared.dialogue import (
    ActionabilityFlagger,
    ActionableItem,
    CallSession,
    CallSessionTrust,
    CompoundCommandSplitter,
    DialogueContext,
    DialogueOrchestrator,
    Entity,
    MultiIntentDecomposer,
    ResponseComposer,
    SubIntent,
    ToolResult,
    TrustLevel,
)


# ═══════════════════════════════════════════════════════════════════════════════
# DialogueContext
# ═══════════════════════════════════════════════════════════════════════════════


class TestDialogueContext:
    def test_add_and_resolve_entity(self):
        ctx = DialogueContext()
        ctx.add_entity(Entity(name="Disha", entity_type="person"))
        resolved = ctx.resolve_reference("disha")
        assert resolved is not None
        assert isinstance(resolved, Entity)
        assert resolved.name == "Disha"

    def test_resolve_alias(self):
        ctx = DialogueContext()
        ctx.add_entity(Entity(name="Disha", aliases=["wife", "spouse"], entity_type="person"))
        resolved = ctx.resolve_reference("wife")
        assert resolved is not None
        assert resolved.name == "Disha"

    def test_resolve_pronoun_her(self):
        ctx = DialogueContext()
        ctx.add_entity(Entity(name="Disha", entity_type="person"))
        resolved = ctx.resolve_reference("her")
        assert resolved is not None
        assert resolved.name == "Disha"

    def test_resolve_type_based(self):
        ctx = DialogueContext()
        ctx.add_entity(Entity(name="the mail from boss", entity_type="email"))
        resolved = ctx.resolve_reference("the mail")
        assert resolved is not None
        assert isinstance(resolved, Entity)

    def test_resolve_reference_id(self):
        ctx = DialogueContext()
        ctx.add_result(ToolResult(tool="gmail.read_unread", result={}, reference_id="the mail"))
        resolved = ctx.resolve_reference("the mail")
        assert resolved is not None
        assert isinstance(resolved, ToolResult)

    def test_resolve_unmatched_returns_none(self):
        ctx = DialogueContext()
        assert ctx.resolve_reference("nonexistent") is None

    def test_last_entity_of_type(self):
        ctx = DialogueContext()
        ctx.add_entity(Entity(name="Alice", entity_type="person"))
        ctx.add_entity(Entity(name="Bob", entity_type="person"))
        last = ctx.last_entity_of_type("person")
        assert last.name == "Bob"

    def test_last_result(self):
        ctx = DialogueContext()
        ctx.add_result(ToolResult(tool="a", result={"x": 1}))
        ctx.add_result(ToolResult(tool="b", result={"y": 2}))
        last = ctx.last_result()
        assert last.tool == "b"

    def test_max_entities_limit(self):
        ctx = DialogueContext(max_entities=3)
        for i in range(5):
            ctx.add_entity(Entity(name=f"Entity{i}", entity_type="other"))
        assert len(ctx._entities) == 3
        assert ctx._entities[0].name == "Entity2"

    def test_turn_counting(self):
        ctx = DialogueContext()
        assert ctx.turn_count == 0
        ctx.increment_turn()
        ctx.increment_turn()
        assert ctx.turn_count == 2

    def test_clear(self):
        ctx = DialogueContext()
        ctx.add_entity(Entity(name="test", entity_type="other"))
        ctx.increment_turn()
        ctx.clear()
        assert len(ctx._entities) == 0
        assert ctx.turn_count == 0

    def test_snapshot(self):
        ctx = DialogueContext()
        ctx.add_entity(Entity(name="Disha", entity_type="person", channel="whatsapp"))
        snap = ctx.snapshot()
        assert len(snap["entities"]) == 1
        assert snap["entities"][0]["name"] == "Disha"


# ═══════════════════════════════════════════════════════════════════════════════
# MultiIntentDecomposer
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiIntentDecomposer:
    def test_single_intent(self):
        d = MultiIntentDecomposer()
        intents = d.decompose("what time is it")
        assert len(intents) == 1
        assert intents[0].intent_type == "query"

    def test_two_intents_with_also(self):
        d = MultiIntentDecomposer()
        intents = d.decompose("yes, also tell me which mails I got")
        assert len(intents) >= 2

    def test_three_intents(self):
        d = MultiIntentDecomposer()
        intents = d.decompose("open vs code, also check my email, and tell Disha I'll be late")
        assert len(intents) >= 2

    def test_negation_detection(self):
        d = MultiIntentDecomposer()
        intents = d.decompose("don't reply to the mail")
        assert len(intents) >= 1
        assert any(i.negated for i in intents)

    def test_empty_utterance(self):
        d = MultiIntentDecomposer()
        intents = d.decompose("")
        assert len(intents) == 0

    def test_query_detection(self):
        d = MultiIntentDecomposer()
        intents = d.decompose("what emails have I received")
        assert intents[0].intent_type == "query"

    def test_approval_detection(self):
        d = MultiIntentDecomposer()
        intents = d.decompose("yes")
        assert intents[0].intent_type == "approval"

    def test_rejection_detection(self):
        d = MultiIntentDecomposer()
        intents = d.decompose("no")
        assert intents[0].intent_type == "rejection"

    def test_with_context(self):
        ctx = DialogueContext()
        ctx.add_entity(Entity(name="Disha", entity_type="person"))
        d = MultiIntentDecomposer()
        intents = d.decompose("tell Disha I'll be late", ctx)
        assert len(intents) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# ActionabilityFlagger
# ═══════════════════════════════════════════════════════════════════════════════


class TestActionabilityFlagger:
    def test_flags_asking_signal(self):
        flagger = ActionabilityFlagger()
        items = [{"sender": "Boss", "subject": "Meeting", "summary": "Can you confirm the meeting?"}]
        actionable = flagger.flag(items, "email")
        assert len(actionable) == 1
        assert actionable[0].sender == "Boss"

    def test_flags_urgent_signal(self):
        flagger = ActionabilityFlagger()
        items = [{"sender": "HR", "summary": "URGENT: sign the contract by Friday"}]
        actionable = flagger.flag(items, "email")
        assert len(actionable) == 1
        assert actionable[0].urgency == "high"

    def test_no_signal_no_flag(self):
        flagger = ActionabilityFlagger()
        items = [{"sender": "Newsletter", "summary": "Weekly tech news digest"}]
        actionable = flagger.flag(items, "email")
        assert len(actionable) == 0

    def test_message_source(self):
        flagger = ActionabilityFlagger()
        items = [{"sender": "Disha", "summary": "Hey, are you coming home? Waiting for your reply."}]
        actionable = flagger.flag(items, "message")
        assert len(actionable) == 1
        assert actionable[0].suggested_action == "reply"

    def test_empty_items(self):
        flagger = ActionabilityFlagger()
        assert flagger.flag([], "email") == []


# ═══════════════════════════════════════════════════════════════════════════════
# CompoundCommandSplitter
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompoundCommandSplitter:
    def test_single_command(self):
        splitter = CompoundCommandSplitter()
        cmds = splitter.split("tell Disha I'll be late")
        assert len(cmds) >= 1

    def test_negation_isolation(self):
        splitter = CompoundCommandSplitter()
        cmds = splitter.split("don't reply to the mail, but tell Disha I'll be late")
        # Negation should only apply to the mail reply
        negated = [c for c in cmds if c.negated]
        non_negated = [c for c in cmds if not c.negated]
        assert len(negated) >= 1
        assert len(non_negated) >= 1

    def test_order_preserved(self):
        splitter = CompoundCommandSplitter()
        cmds = splitter.split("first check email, then tell Disha")
        assert len(cmds) >= 2
        assert cmds[0].order < cmds[-1].order


# ═══════════════════════════════════════════════════════════════════════════════
# ResponseComposer
# ═══════════════════════════════════════════════════════════════════════════════


class TestResponseComposer:
    def test_compose_email_result(self):
        composer = ResponseComposer()
        results = [{"sender": "Boss", "subject": "Meeting", "urgency": "high"}]
        resp = composer.compose(results)
        assert "Boss" in resp.spoken
        assert "Meeting" in resp.spoken

    def test_compose_message_result(self):
        composer = ResponseComposer()
        results = [{"sender": "Disha", "summary": "Are you coming home?"}]
        resp = composer.compose(results)
        assert "Disha" in resp.spoken

    def test_compose_multiple_results(self):
        composer = ResponseComposer()
        results = [
            {"sender": "Boss", "subject": "Meeting"},
            {"sender": "Disha", "summary": "Dinner tonight?"},
        ]
        resp = composer.compose(results)
        assert "Boss" in resp.spoken
        assert "disha" in resp.spoken.lower()
        assert "and" in resp.spoken.lower()

    def test_compose_empty(self):
        composer = ResponseComposer()
        resp = composer.compose([])
        assert "nothing" in resp.spoken.lower()

    def test_compose_with_actionables(self):
        composer = ResponseComposer()
        results = [{"sender": "Boss", "subject": "Meeting"}]
        actionables = [ActionableItem(source="email", sender="Boss", summary="Meeting", suggested_action="reply")]
        resp = composer.compose(results, actionables=actionables)
        assert resp.follow_up  # should have a follow-up question

    def test_calendar_result(self):
        composer = ResponseComposer()
        results = [{"title": "team standup", "time": "at 3pm"}]
        resp = composer.compose(results)
        assert "team standup" in resp.spoken
        assert "3pm" in resp.spoken


# ═══════════════════════════════════════════════════════════════════════════════
# CallSessionTrust
# ═══════════════════════════════════════════════════════════════════════════════


class TestCallSessionTrust:
    def test_start_session(self):
        trust = CallSessionTrust()
        session = trust.start_session()
        assert session.trust_level == TrustLevel.UNVERIFIED
        assert not session.is_verified()

    def test_voiceprint_verify(self):
        def checker(audio, sr):
            return True, "verified"

        trust = CallSessionTrust(voiceprint_checker=checker)
        trust.start_session()
        passed, _ = trust.verify_voiceprint(np.zeros(16000, dtype=np.float32))
        assert passed
        assert trust.can_execute()

    def test_voiceprint_reject(self):
        def checker(audio, sr):
            return False, "no match"

        trust = CallSessionTrust(voiceprint_checker=checker)
        trust.start_session()
        passed, _ = trust.verify_voiceprint(np.zeros(16000, dtype=np.float32))
        assert not passed
        assert not trust.can_execute()

    def test_critical_always_needs_explicit_confirm(self):
        def checker(audio, sr):
            return True, "verified"

        trust = CallSessionTrust(voiceprint_checker=checker)
        trust.start_session()
        trust.verify_voiceprint(np.zeros(16000, dtype=np.float32))
        # Even after voiceprint, critical needs explicit confirm
        assert not trust.can_critical()
        trust.confirm_critical()
        # confirm_critical returns True but can_critical is still False
        # (must call confirm_critical again for each critical action)

    def test_no_session_can_execute_fails(self):
        trust = CallSessionTrust()
        assert not trust.can_execute()

    def test_end_session(self):
        def checker(audio, sr):
            return True, "verified"

        trust = CallSessionTrust(voiceprint_checker=checker)
        trust.start_session()
        trust.verify_voiceprint(np.zeros(16000, dtype=np.float32))
        trust.end_session()
        assert trust.session is None
        assert not trust.can_execute()

    def test_no_voiceprint_checker_fails(self):
        trust = CallSessionTrust(voiceprint_checker=None)
        trust.start_session()
        passed, reason = trust.verify_voiceprint(np.zeros(16000, dtype=np.float32))
        assert not passed
        assert "not configured" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# DialogueOrchestrator (integration)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDialogueOrchestrator:
    def test_start_and_end_call(self):
        orch = DialogueOrchestrator()
        session = orch.start_call()
        assert session.trust_level == TrustLevel.UNVERIFIED
        orch.end_call()
        assert orch.trust.session is None

    def test_process_single_intent(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        result = orch.process("what time is it")
        assert len(result.sub_intents) == 1
        assert result.sub_intents[0].intent_type == "query"

    def test_process_multi_intent(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        result = orch.process("yes, also tell me which mails I received")
        assert len(result.sub_intents) >= 2

    def test_process_with_tool_results(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        results = [
            {"sender": "Boss", "subject": "Urgent meeting", "urgency": "high"},
            {"sender": "Disha", "summary": "Dinner tonight?"},
        ]
        result = orch.process("check my messages", tool_results=results, source_type="email")
        assert result.composed_response is not None
        assert "Boss" in result.composed_response.spoken

    def test_process_compound_command(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        result = orch.process(
            "don't reply to the mail, just react with a thumbs up, and tell Disha I'll be late"
        )
        assert len(result.split_commands) >= 2
        negated = [c for c in result.split_commands if c.negated]
        assert len(negated) >= 1

    def test_entity_tracking(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        orch.process("tell Disha I'll be late")
        # Disha should be tracked
        entity = orch.context.last_entity_of_type("person")
        assert entity is not None
        assert entity.name == "Disha"

    def test_context_persists_across_turns(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        orch.process("tell Disha I'll be late")
        # Next turn should have context
        result = orch.process("what about Bob?")
        assert orch.context.turn_count == 2

    def test_actionables_from_results(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        results = [{"sender": "Boss", "summary": "Can you confirm the meeting?"}]
        result = orch.process("check email", tool_results=results)
        assert len(result.actionables) >= 1

    def test_trust_level_in_result(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        result = orch.process("hello")
        assert result.trust_level == TrustLevel.UNVERIFIED

    def test_requires_approval_for_commands(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        result = orch.process("send a message to Bob")
        assert result.requires_approval is True

    def test_no_approval_for_queries(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        result = orch.process("what time is it")
        assert result.requires_approval is False

    def test_context_snapshot(self):
        orch = DialogueOrchestrator()
        orch.start_call()
        result = orch.process("hello")
        assert "turn_count" in result.context_snapshot
        assert result.context_snapshot["turn_count"] == 1
