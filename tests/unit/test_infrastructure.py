"""Tests for Phase 9 — Infrastructure (Celery, migrations, Tailscale, LLM fallbacks)."""

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from packages.shared.infrastructure import (
    LLMResponse,
    LLMRouter,
    Migration,
    MigrationRunner,
    TaskQueue,
    TaskResult,
    TaskStatus,
    TailscaleManager,
    TailscalePeer,
)


# --- Phase 9.1: Task Queue Tests ---


class TestTaskQueue:
    def test_register_and_submit(self):
        q = TaskQueue()
        q.register("add", lambda a, b: a + b)
        result = q.submit("add", 2, 3)
        assert result.status == TaskStatus.SUCCESS
        assert result.result == 5

    def test_submit_unknown_task(self):
        q = TaskQueue()
        result = q.submit("nonexistent")
        assert result.status == TaskStatus.FAILURE
        assert "Unknown task" in result.error

    def test_submit_failure(self):
        q = TaskQueue()
        q.register("fail", lambda: 1 / 0)
        result = q.submit("fail")
        assert result.status == TaskStatus.FAILURE
        assert "division by zero" in result.error

    def test_get_result(self):
        q = TaskQueue()
        q.register("noop", lambda: "ok")
        result = q.submit("noop")
        fetched = q.get_result(result.task_id)
        assert fetched is not None
        assert fetched.task_id == result.task_id

    def test_revoke(self):
        q = TaskQueue()
        q.register("noop", lambda: "ok")
        result = q.submit("noop")
        revoked = q.revoke(result.task_id)
        assert not revoked  # already completed

    def test_list_tasks(self):
        q = TaskQueue()
        q.register("noop", lambda: "ok")
        q.submit("noop")
        tasks = q.list_tasks()
        assert len(tasks) == 1

    def test_task_result_to_dict(self):
        result = TaskResult(task_id="t1", status=TaskStatus.SUCCESS, result="done")
        d = result.to_dict()
        assert d["task_id"] == "t1"
        assert d["status"] == "success"
        assert d["result"] == "done"


# --- Phase 9.2: Migration Runner Tests ---


class TestMigrationRunner:
    def test_load_migrations_empty(self, tmp_path):
        runner = MigrationRunner()
        runner.migrations_dir = tmp_path / "migrations"
        assert runner.get_pending() == []

    def test_load_migrations_with_files(self, tmp_path):
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "001_init.sql").write_text("CREATE TABLE users (id INT);")
        (migrations_dir / "002_add_email.sql").write_text("ALTER TABLE users ADD COLUMN email TEXT;")

        runner = MigrationRunner()
        runner.migrations_dir = migrations_dir
        pending = runner.get_pending()
        assert len(pending) == 2
        assert pending[0].version == "001"
        assert pending[1].version == "002"

    def test_apply_migration(self):
        runner = MigrationRunner()
        m = Migration(version="001", name="init", up_sql="CREATE TABLE t1 (id INT);", down_sql="DROP TABLE t1;")
        runner.apply(m)
        assert runner.current_version() == "001"
        assert "001" in runner.get_applied()

    def test_rollback_migration(self):
        runner = MigrationRunner()
        m = Migration(version="001", name="init", up_sql="CREATE TABLE t1 (id INT);", down_sql="DROP TABLE t1;")
        runner.apply(m)
        runner.rollback(m)
        assert runner.current_version() is None

    def test_pending_after_apply(self, tmp_path):
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "001_init.sql").write_text("CREATE TABLE t1 (id INT);")
        (migrations_dir / "002_add.sql").write_text("ALTER TABLE t1 ADD COLUMN x TEXT;")

        runner = MigrationRunner()
        runner.migrations_dir = migrations_dir
        m1 = Migration(version="001", name="init", up_sql="CREATE TABLE t1 (id INT);", down_sql="DROP TABLE t1;")
        runner.apply(m1)
        pending = runner.get_pending()
        assert len(pending) == 1
        assert pending[0].version == "002"

    def test_migration_checksum(self):
        m = Migration(version="001", name="init", up_sql="CREATE TABLE t1 (id INT);", down_sql="")
        assert len(m.checksum) == 8

    def test_migration_with_down_sql(self, tmp_path):
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "001_init.sql").write_text("CREATE TABLE t1 (id INT);\n-- DOWN\nDROP TABLE t1;")
        runner = MigrationRunner()
        runner.migrations_dir = migrations_dir
        pending = runner.get_pending()
        assert pending[0].down_sql == "DROP TABLE t1;"

    def test_current_version_empty(self):
        runner = MigrationRunner()
        assert runner.current_version() is None


# --- Phase 9.3: Tailscale Tests ---


class TestTailscaleManager:
    def test_init(self):
        ts = TailscaleManager()
        assert ts.tailscale_path == "tailscale"

    def test_status_not_installed(self):
        ts = TailscaleManager(tailscale_path="nonexistent_tailscale")
        status = ts.status()
        assert not status["connected"]
        assert "not installed" in status["error"]

    def test_get_peers_empty(self):
        ts = TailscaleManager()
        peers = ts.get_peers()
        assert peers == []

    def test_find_peer_not_found(self):
        ts = TailscaleManager()
        peer = ts.find_peer("nonexistent")
        assert peer is None

    def test_is_reachable(self):
        ts = TailscaleManager()
        assert not ts.is_reachable("anyone")


# --- Phase 9.4: LLM Router Tests ---


class TestLLMRouter:
    def test_default_provider(self):
        router = LLMRouter()
        assert router.select_provider() == "ollama"
        assert router.is_local()

    def test_select_preferred(self):
        router = LLMRouter(preferred_provider="groq")
        assert router.select_provider() == "groq"
        assert not router.is_local()

    def test_fallback_when_preferred_down(self):
        router = LLMRouter(preferred_provider="groq")
        router.set_provider_status("groq", False)
        selected = router.select_provider()
        assert selected != "groq"

    def test_all_down_returns_ollama(self):
        router = LLMRouter()
        for p in ["groq", "openrouter", "gemini"]:
            router.set_provider_status(p, False)
        selected = router.select_provider("groq")
        assert selected == "ollama"

    def test_get_config_ollama(self):
        router = LLMRouter()
        cfg = router.get_config("ollama")
        assert "base_url" in cfg
        assert "localhost" in cfg["base_url"]

    def test_get_config_with_env(self):
        router = LLMRouter()
        with patch.dict(os.environ, {"GROQ_API_KEY": "test_key"}):
            cfg = router.get_config("groq")
            assert cfg["api_key"] == "test_key"

    def test_llm_response(self):
        resp = LLMResponse(content="Hello", model="gpt-4", provider="openrouter", tokens_used=10, latency_ms=100)
        assert resp.content == "Hello"
        assert resp.tokens_used == 10
        assert not resp.cached

    def test_select_unknown_falls_back(self):
        router = LLMRouter()
        selected = router.select_provider("nonexistent")
        assert selected in router.PROVIDERS
