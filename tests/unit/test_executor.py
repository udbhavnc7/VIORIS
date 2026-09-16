import pytest

from apps.desktop_agent.app.commands import handle
from apps.desktop_agent.app.executor import (
    Executor,
    PermissionBlockedError,
)

# Ensure the Phase 1 registry is populated (idempotent).
from packages.shared.permission_engine import PermissionEngine, register_phase1_tools

register_phase1_tools()


@pytest.fixture(autouse=True)
def _phase1_registry():
    """Other test files call PermissionEngine.reset(), which wipes the shared
    registry. Keep this file's tests deterministic by re-registering Phase 1."""
    PermissionEngine.reset()
    register_phase1_tools()
    yield
    PermissionEngine.reset()


class TestExecutorGates:
    def test_unregistered_tool_is_blocked(self):
        from apps.desktop_agent.app.commands import CommandResult

        ex = Executor()
        cmd = CommandResult(
            tool="system.delete_everything", action="system.delete_everything", detail={}, reply=""
        )
        try:
            ex.execute(cmd)
        except PermissionBlockedError as exc:
            assert "not registered" in str(exc)
        else:
            raise AssertionError("unregistered tool must be blocked")

    def test_observe_tools_run_without_approval(self):
        ex = Executor()
        status, result = ex.execute(handle("what time is it"))
        assert status == "completed"
        assert "time" in result

    def test_open_app_runs_through_injected_launcher(self):
        opened = []
        ex = Executor(open_app_fn=opened.append)
        status, result = ex.execute(handle("open vs code"))
        assert status == "completed"
        assert opened == ["vs code"]
        assert result["launched"] is True


class TestExecutorPaths:
    def test_unknown_command_is_not_blocked(self):
        ex = Executor()
        status, result = ex.execute(handle("sing me a song"))
        assert status == "completed"
        assert "not understood" in result.get("note", "")

    def test_open_app_without_target_raises(self):
        from apps.desktop_agent.app.commands import CommandResult

        ex = Executor()
        cmd = CommandResult(tool="system.open_app", action="system.open_app", detail={}, reply="")
        try:
            ex.execute(cmd)
        except PermissionBlockedError as exc:
            assert "app target" in str(exc)
        else:
            raise AssertionError("open_app without target must be blocked")
