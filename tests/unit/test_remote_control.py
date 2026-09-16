"""Phase 5.3 remote-control tests: session auto-expiry gating at the agent unit
level, and task-runner executor routing to the computer agent daemon."""

import time

import pytest

from agents.computer.app.remote_control import (
    InputBackendError,
    RemoteControl,
    RemoteSessionError,
    RemoteSessionManager,
)


class TestRemoteSessionManager:
    def test_create_and_require_active(self):
        mgr = RemoteSessionManager(ttl_seconds=600)
        session = mgr.create("phone-1", timeout_minutes=10)
        assert mgr.require_active(session.session_id, "phone-1").session_id == session.session_id

    def test_session_belongs_to_another_device(self):
        mgr = RemoteSessionManager()
        session = mgr.create("phone-1")
        with pytest.raises(RemoteSessionError):
            mgr.require_active(session.session_id, "phone-2")

    def test_expired_session_refuses(self):
        mgr = RemoteSessionManager(ttl_seconds=1)
        session = mgr.create("phone-1", timeout_minutes=None)
        time.sleep(1.05)
        with pytest.raises(RemoteSessionError):
            mgr.require_active(session.session_id, "phone-1")
        assert mgr.active_for_device("phone-1") is None

    def test_ttl_capped_at_max(self):
        mgr = RemoteSessionManager()
        session = mgr.create("phone-1", timeout_minutes=9999)
        assert session.expires_at - session.started_at <= 3600

    def test_unknown_session(self):
        mgr = RemoteSessionManager()
        with pytest.raises(RemoteSessionError):
            mgr.require_active("missing", "phone-1")

    def test_end_all_for_device_kills_every_live_session(self):
        mgr = RemoteSessionManager(ttl_seconds=600)
        a = mgr.create("phone-1", timeout_minutes=10)
        b = mgr.create("phone-1", timeout_minutes=10)
        mgr.create("phone-2", timeout_minutes=10)
        assert mgr.end_all_for_device("phone-1") == 2
        with pytest.raises(RemoteSessionError):
            mgr.require_active(a.session_id, "phone-1")
        with pytest.raises(RemoteSessionError):
            mgr.require_active(b.session_id, "phone-1")


class TestRemoteControlGating:
    def test_input_without_session_is_refused(self):
        sent = []
        rc = RemoteControl(keyboard_fn=sent.append, mouse_fn=lambda *a: None)
        with pytest.raises(RemoteSessionError):
            rc.send_input("nothing", "phone-1", "type", text="hi")
        assert sent == []

    def test_keyboard_input_only_with_active_session(self):
        sent = []
        rc = RemoteControl(keyboard_fn=sent.append, mouse_fn=lambda *a: None)
        session = rc.sessions.create("phone-1")
        rc.send_input(session.session_id, "phone-1", "type", text="ls -la")
        assert sent == ["ls -la"]

    def test_mouse_input_routes_to_mouse_backend(self):
        moves = []
        rc = RemoteControl(keyboard_fn=lambda s: None, mouse_fn=lambda a, x, y: moves.append((a, x, y)))
        session = rc.sessions.create("phone-1")
        rc.send_input(session.session_id, "phone-1", "move", x=120, y=340)
        assert moves == [("move", 120, 340)]

    def test_unknown_action_rejected(self):
        rc = RemoteControl(keyboard_fn=lambda s: None, mouse_fn=lambda *a: None)
        session = rc.sessions.create("phone-1")
        with pytest.raises(InputBackendError):
            rc.send_input(session.session_id, "phone-1", "swipe")


class TestTaskRunnerExecutor:
    def test_unknown_tool_refused(self):
        from packages.shared.schemas import TaskStep
        from services.task_runner.app.executor import make_executor

        step = TaskStep(agent="computer", tool="computer.something_new", risk_level="execute")
        step.result = {"args": {}}
        with pytest.raises(RuntimeError):
            make_executor("http://127.0.0.1:9")(step)

    def test_remote_session_routes_to_daemon_with_args(self, monkeypatch):
        import services.task_runner.app.executor as executor_mod

        captured = {}
        monkeypatch.setattr(
            executor_mod,
            "_call_computer",
            lambda tool, args, base: captured.setdefault("calls", []).append((tool, args, base)) or {"ok": True, "result": {}},
        )
        from packages.shared.schemas import TaskStep
        from services.task_runner.app.executor import make_executor

        step = TaskStep(agent="computer", tool="computer.start_remote_session", risk_level="execute")
        step.result = {"args": {"device_id": "phone-1", "timeout_minutes": 5}}
        result = make_executor("http://127.0.0.1:9")(step)

        assert captured["calls"] == [
            ("computer.start_remote_session", {"device_id": "phone-1", "timeout_minutes": 5}, "http://127.0.0.1:9")
        ]
        assert result["ok"] is True