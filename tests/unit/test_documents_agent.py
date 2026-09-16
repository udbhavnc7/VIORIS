"""Documents agent unit tests: allow-list guardrails (Prompt 3.3).

The allow-list defaults to EMPTY, so with no config every file/tool is refused.
With a temp dir granted, reads/search/moves work; delete is Critical; anything
outside the granted roots or the command allow-list is REFUSED, never attempted.
"""

import pytest

from agents.documents.app.agent import DocumentsAgent, PermissionBlockedError
from agents.documents.app.allowlist import GuardrailConfig, path_is_allowed
from packages.shared.permission_engine import PermissionEngine, register_phase3_files_tools

register_phase3_files_tools()


@pytest.fixture(autouse=True)
def _registry():
    PermissionEngine.reset()
    register_phase3_files_tools()
    yield
    PermissionEngine.reset()


def make_agent(tmp_path, shell: str | None = None) -> DocumentsAgent:
    return DocumentsAgent(
        config=GuardrailConfig(
            allowed_dirs=(tmp_path.resolve(),),
            shell_programs=tuple(shell.split(",")) if shell else (),
        )
    )


class TestDefaultEmptyAllowlist:
    def test_agent_with_no_config_can_refuse_everything(self, tmp_path):
        agent = DocumentsAgent(config=GuardrailConfig())
        f = tmp_path / "secret.txt"
        f.write_text("classified")
        out = agent.read_file(str(f))
        assert not out.ok and out.blocked == "outside-allowlist"

    def test_path_is_allowed_requires_exact_root_match(self, tmp_path):
        root = tmp_path.resolve()
        assert path_is_allowed((root,), root / "a" / "b.txt")
        sibling = tmp_path.parent / "other.txt"
        assert not path_is_allowed((root,), sibling)

    def test_empty_allowlist_blocks_move_and_delete(self, tmp_path):
        agent = DocumentsAgent(GuardrailConfig())
        out = agent.move(str(tmp_path / "a.txt"), str(tmp_path / "b.txt"))
        assert not out.ok and out.blocked == "outside-allowlist"
        out = agent.delete(str(tmp_path / "a.txt"))
        assert not out.ok and out.blocked == "outside-allowlist"


class TestObserveOps:
    def test_search_finds_only_inside_allowed_root(self, tmp_path):
        (tmp_path / "report.md").write_text("hi")
        agent = make_agent(tmp_path)
        out = agent.search_files("report")
        assert out.ok and out.detail["count"] == 1
        assert out.detail["matches"] == ["report.md"]

    def test_search_out_of_root_refused(self, tmp_path):
        agent = make_agent(tmp_path)
        out = agent.search_files("anything", root=str(tmp_path.parent))
        assert not out.ok and out.blocked == "outside-allowlist"

    def test_read_inside_root_ok(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        agent = make_agent(tmp_path)
        out = agent.read_file(str(tmp_path / "notes.txt"))
        assert out.ok and "hello" in out.detail["preview"]

    def test_read_outside_root_refused(self, tmp_path):
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("x")
        agent = make_agent(tmp_path)
        out = agent.read_file(str(outside))
        assert not out.ok and out.blocked == "outside-allowlist"


class TestExecuteOps:
    def test_move_inside_root_ok(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        agent = make_agent(tmp_path)
        out = agent.move(str(tmp_path / "a.txt"), str(tmp_path / "b.txt"))
        assert out.ok and out.detail["destination"].endswith("b.txt")
        assert (tmp_path / "b.txt").exists()

    def test_move_with_destination_outside_root_refused(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        agent = make_agent(tmp_path)
        out = agent.move(str(tmp_path / "a.txt"), str(tmp_path.parent / "b.txt"))
        assert not out.ok and out.blocked == "outside-allowlist"
        assert (tmp_path / "a.txt").exists()

    def test_rename_bare_name_enforced(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        agent = make_agent(tmp_path)
        out = agent.rename(str(tmp_path / "a.txt"), "../escape")
        assert not out.ok and "bare filename" in out.note

    def test_rename_inside_root_ok(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        agent = make_agent(tmp_path)
        out = agent.rename(str(tmp_path / "a.txt"), "b.txt")
        assert out.ok and (tmp_path / "b.txt").exists()


class TestCriticalDelete:
    def test_delete_inside_root_executes_but_marks_critical_gate(self, tmp_path):
        (tmp_path / "del.txt").write_text("x")
        agent = make_agent(tmp_path)
        out = agent.delete(str(tmp_path / "del.txt"))
        assert out.ok
        assert out.detail["critical_gate_required"] is True
        assert not (tmp_path / "del.txt").exists()

    def test_delete_registered_as_critical(self):
        reg = PermissionEngine.classify("file.delete")
        assert reg.tier.value == "critical"
        assert reg.requires_second_factor is True
        assert reg.cooldown_seconds > 0

    def test_delete_outside_root_refused_even_though_critical(self, tmp_path):
        agent = make_agent(tmp_path)
        out = agent.delete(str(tmp_path.parent / "thing.txt"))
        assert not out.ok and out.blocked == "outside-allowlist"


class TestTerminalAllowlist:
    def test_no_shell_allowlist_refuses_every_command(self):
        agent = DocumentsAgent(GuardrailConfig())
        out = agent.run("ping")
        assert not out.ok and out.blocked == "command-allowlist"
        assert "refused" in out.note

    def test_allowlisted_program_runs(self, tmp_path):
        agent = make_agent(tmp_path, shell="python")
        out = agent.run("python", ["-c", "print(2)  # noqa: BLE001"])
        assert out.ok and out.detail["returncode"] == 0

    def test_non_allowlisted_program_refused_not_attempted(self, tmp_path):
        agent = make_agent(tmp_path, shell="python")
        out = agent.run("powershell", ["pwd"])
        assert not out.ok and out.blocked == "command-allowlist"

    def test_pipelines_and_shell_metachars_refused(self, tmp_path):
        agent = make_agent(tmp_path, shell="python")
        assert not agent.run("python; rm -rf /").ok
        assert not agent.run("git | grep x").ok


class TestRegistryGate:
    def test_unregistered_file_op_is_never_attempted(self):
        agent = DocumentsAgent()
        with pytest.raises(PermissionBlockedError):
            agent.classify_with_gate("file.harvest_credentials")

    def test_vip_terminal_execute_requires_confirmation(self):
        assert PermissionEngine.classify("terminal.run").confirmation_required is True
        assert PermissionEngine.classify("file.move").confirmation_required is True
