"""Tests for Phase 13 (Flutter UI), Phase 14 (Remote Access), Phase 15 (Learning)."""

import asyncio
import json
import os
import tempfile

import pytest
from mobile.flutter.ui_models import (
    ApprovalCardModel,
    CallScreenModel,
    CallScreenMode,
    ContactModel,
    DashboardStats,
    HistoryItem,
    ScreenType,
    SettingsModel,
    UIEvent,
)
from packages.shared.remote_access import (
    PhoneControlCenter,
    RemoteCommand,
    RemoteRequest,
    RemoteResponse,
    TailscaleBridge,
)
from packages.shared.learning import (
    AdaptiveResponder,
    LearnedPattern,
    PatternLearner,
    PreferenceStore,
    UserPreference,
)


# --- Phase 13: Flutter UI Tests ---


class TestCallScreenModel:
    def test_defaults(self):
        model = CallScreenModel()
        assert model.mode == CallScreenMode.RINGING
        assert not model.is_muted

    def test_to_dict(self):
        model = CallScreenModel(mode=CallScreenMode.ACTIVE, caller_name="Disha", duration_seconds=60)
        d = model.to_dict()
        assert d["mode"] == "active"
        assert d["caller_name"] == "Disha"
        assert d["duration_seconds"] == 60


class TestApprovalCardModel:
    def test_to_dict(self):
        card = ApprovalCardModel(action_id="a1", tool="email", description="Send email to Disha")
        d = card.to_dict()
        assert d["action_id"] == "a1"
        assert d["tool"] == "email"
        assert d["risk_level"] == "observe"


class TestSettingsModel:
    def test_defaults(self):
        settings = SettingsModel()
        assert settings.wake_word_enabled
        assert settings.theme == "dark"

    def test_update(self):
        settings = SettingsModel()
        settings.update(theme="light", language="es")
        assert settings.theme == "light"
        assert settings.language == "es"

    def test_to_dict(self):
        settings = SettingsModel()
        d = settings.to_dict()
        assert "wake_word_enabled" in d
        assert "digest_hours" in d


class TestContactModel:
    def test_to_dict(self):
        contact = ContactModel(name="Disha", phone="123", email="d@test.com", is_favorite=True)
        d = contact.to_dict()
        assert d["name"] == "Disha"
        assert d["is_favorite"]


class TestHistoryItem:
    def test_to_dict(self):
        item = HistoryItem(timestamp="2025-01-15", action="send email", tool="email", success=True)
        d = item.to_dict()
        assert d["success"]


class TestDashboardStats:
    def test_to_dict(self):
        stats = DashboardStats(total_actions=100, successful_actions=95)
        d = stats.to_dict()
        assert d["total_actions"] == 100
        assert d["successful_actions"] == 95


class TestUIEvent:
    def test_to_json_roundtrip(self):
        event = UIEvent(event_type="call_started", payload={"call_id": "c1"})
        raw = event.to_json()
        restored = UIEvent.from_json(raw)
        assert restored.event_type == "call_started"
        assert restored.payload["call_id"] == "c1"


class TestScreenType:
    def test_values(self):
        assert ScreenType.CALL.value == "call"
        assert ScreenType.APPROVAL.value == "approval"
        assert ScreenType.SETTINGS.value == "settings"


# --- Phase 14: Remote Access Tests ---


class TestRemoteRequest:
    def test_to_json_roundtrip(self):
        req = RemoteRequest(command=RemoteCommand.PING, params={"key": "value"}, request_id="r1")
        raw = req.to_json()
        restored = RemoteRequest.from_json(raw)
        assert restored.command == RemoteCommand.PING
        assert restored.params["key"] == "value"


class TestRemoteResponse:
    def test_to_json_roundtrip(self):
        resp = RemoteResponse(request_id="r1", success=True, data={"status": "ok"})
        raw = resp.to_json()
        restored = RemoteResponse.from_json(raw)
        assert restored.success
        assert restored.data["status"] == "ok"


class TestPhoneControlCenter:
    def test_init(self):
        pcc = PhoneControlCenter()
        assert len(pcc.get_command_history()) == 0

    @pytest.mark.asyncio
    async def test_handle_ping(self):
        pcc = PhoneControlCenter()
        req = RemoteRequest(command=RemoteCommand.PING, request_id="r1")
        resp = await pcc.handle(req)
        assert resp.success
        assert resp.data["pong"]

    @pytest.mark.asyncio
    async def test_handle_status(self):
        pcc = PhoneControlCenter()
        req = RemoteRequest(command=RemoteCommand.STATUS, request_id="r1")
        resp = await pcc.handle(req)
        assert resp.success
        assert "connected_devices" in resp.data

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self):
        pcc = PhoneControlCenter()
        req = RemoteRequest(command=RemoteCommand.EXECUTE_ACTION, request_id="r1")
        resp = await pcc.handle(req)
        assert not resp.success
        assert "Unknown command" in resp.error

    def test_register_unregister_device(self):
        pcc = PhoneControlCenter()
        pcc.register_device("phone1", "phone", "My Phone")
        assert len(pcc.get_devices()) == 1
        pcc.unregister_device("phone1")
        assert len(pcc.get_devices()) == 0

    @pytest.mark.asyncio
    async def test_custom_handler(self):
        pcc = PhoneControlCenter()
        pcc.on(RemoteCommand.PING, lambda req: {"custom": True})
        req = RemoteRequest(command=RemoteCommand.PING, request_id="r1")
        resp = await pcc.handle(req)
        assert resp.data["custom"]

    def test_command_history(self):
        pcc = PhoneControlCenter()
        pcc._command_history.append({"command": "ping", "success": True})
        assert len(pcc.get_command_history()) == 1


class TestTailscaleBridge:
    def test_init(self):
        bridge = TailscaleBridge()
        assert bridge.connected_count == 0

    def test_control_center(self):
        bridge = TailscaleBridge()
        assert isinstance(bridge.control_center, PhoneControlCenter)


# --- Phase 15: Learning & Personalization Tests ---


class TestUserPreference:
    def test_to_dict(self):
        pref = UserPreference(key="theme", value="dark", category="appearance")
        d = pref.to_dict()
        assert d["key"] == "theme"
        assert d["value"] == "dark"


class TestPreferenceStore:
    def test_set_and_get(self):
        store = PreferenceStore()
        store.set("theme", "dark")
        assert store.get("theme") == "dark"

    def test_get_default(self):
        store = PreferenceStore()
        assert store.get("nonexistent", "default") == "default"

    def test_remove(self):
        store = PreferenceStore()
        store.set("theme", "dark")
        assert store.remove("theme")
        assert store.get("theme") is None

    def test_remove_nonexistent(self):
        store = PreferenceStore()
        assert not store.remove("nonexistent")

    def test_list_all(self):
        store = PreferenceStore()
        store.set("theme", "dark")
        store.set("language", "en")
        assert len(store.list_all()) == 2

    def test_list_by_category(self):
        store = PreferenceStore()
        store.set("theme", "dark", category="appearance")
        store.set("language", "en", category="general")
        assert len(store.list_by_category("appearance")) == 1

    def test_export_import(self):
        store = PreferenceStore()
        store.set("theme", "dark")
        exported = store.export()
        store2 = PreferenceStore()
        store2.import_prefs(exported)
        assert store2.get("theme") == "dark"

    def test_persist(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            store = PreferenceStore(persist_path=path)
            store.set("theme", "dark")
            store2 = PreferenceStore(persist_path=path)
            assert store2.get("theme") == "dark"
        finally:
            os.unlink(path)


class TestLearnedPattern:
    def test_to_dict(self):
        pattern = LearnedPattern(pattern_id="p1", trigger="hello", response="hi", frequency=5)
        d = pattern.to_dict()
        assert d["frequency"] == 5


class TestPatternLearner:
    def test_record(self):
        learner = PatternLearner()
        learner.record("hello", "hi")
        assert len(learner.list_all()) == 1

    def test_record_increases_frequency(self):
        learner = PatternLearner()
        learner.record("hello", "hi")
        learner.record("hello", "hi")
        patterns = learner.list_all()
        assert patterns[0].frequency == 2
        assert patterns[0].confidence > 0.5

    def test_find_match(self):
        learner = PatternLearner()
        learner.record("hello there", "hi there")
        match = learner.find_match("hello there")
        assert match is not None

    def test_find_match_no_result(self):
        learner = PatternLearner()
        match = learner.find_match("random")
        assert match is None

    def test_get_suggestions(self):
        learner = PatternLearner()
        learner.record("hello there", "hi")
        learner.record("hello world", "hi")
        suggestions = learner.get_suggestions("hello")
        assert len(suggestions) >= 1

    def test_prune(self):
        learner = PatternLearner()
        learner.record("a", "b")
        pruned = learner.prune(min_frequency=3, min_confidence=0.8)
        assert pruned == 1

    def test_similarity(self):
        learner = PatternLearner()
        score = learner._similarity("hello world", "hello there")
        assert 0.0 < score < 1.0

    def test_persist(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            learner = PatternLearner(persist_path=path)
            learner.record("hello", "hi")
            learner2 = PatternLearner(persist_path=path)
            assert len(learner2.list_all()) == 1
        finally:
            os.unlink(path)


class TestAdaptiveResponder:
    def test_get_greeting(self):
        prefs = PreferenceStore()
        prefs.set("user_name", "Disha")
        prefs.set("response_style", "casual")
        patterns = PatternLearner()
        responder = AdaptiveResponder(prefs, patterns)
        greeting = responder.get_greeting()
        assert "Disha" in greeting

    def test_adapt_response_verbose(self):
        prefs = PreferenceStore()
        prefs.set("response_style", "verbose")
        patterns = PatternLearner()
        responder = AdaptiveResponder(prefs, patterns)
        result = responder.adapt_response("Email sent.")
        assert "Let me know" in result

    def test_adapt_response_minimal(self):
        prefs = PreferenceStore()
        prefs.set("response_style", "minimal")
        patterns = PatternLearner()
        responder = AdaptiveResponder(prefs, patterns)
        long_response = " ".join(["word"] * 20)
        result = responder.adapt_response(long_response)
        assert "..." in result

    def test_should_auto_approve_observe(self):
        prefs = PreferenceStore()
        prefs.set("auto_approve_observe", True)
        patterns = PatternLearner()
        responder = AdaptiveResponder(prefs, patterns)
        assert responder.should_auto_approve("observe")

    def test_should_auto_approve_critical(self):
        prefs = PreferenceStore()
        prefs.set("auto_approve_observe", True)
        patterns = PatternLearner()
        responder = AdaptiveResponder(prefs, patterns)
        assert not responder.should_auto_approve("critical")
