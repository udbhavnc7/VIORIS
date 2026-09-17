"""Infrastructure — Celery, migrations, Tailscale, LLM fallbacks."""

import json
import os
import subprocess
import time
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


# --- Phase 9.1: Celery + Redis Task Queue ---


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    REVOKED = "revoked"


@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    result: Any = None
    error: Optional[str] = None
    retries: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "retries": self.retries,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class TaskQueue:
    """Lightweight task queue abstraction wrapping Celery or falling back to in-process."""

    def __init__(self, broker_url: Optional[str] = None, backend_url: Optional[str] = None):
        self.broker_url = broker_url or os.getenv("VIORIS_REDIS_URL", "redis://localhost:6379/0")
        self.backend_url = backend_url or self.broker_url
        self._tasks: dict[str, TaskResult] = {}
        self._handlers: dict[str, Callable] = {}
        self._use_celery = False
        self.app = None

    def enable_celery(self):
        """Explicitly enable Celery integration after construction."""
        try:
            from celery import Celery
            self.app = Celery("vioris", broker=self.broker_url, backend=self.backend_url)
            self._use_celery = True
        except ImportError:
            self.app = None

    def register(self, name: str, handler: Callable):
        self._handlers[name] = handler
        if self._use_celery and self.app:
            @self.app.task(bind=True, name=name, max_retries=3)
            def run_task(self_inner, *args, **kwargs):
                try:
                    return handler(*args, **kwargs)
                except Exception as e:
                    self_inner.retry(exc=e, countdown=2 ** self_inner.request.retries)

    def submit(self, task_name: str, *args, **kwargs) -> TaskResult:
        task_id = hashlib.sha256(f"{task_name}{time.time()}".encode()).hexdigest()[:16]
        result = TaskResult(task_id=task_id, status=TaskStatus.PENDING)
        self._tasks[task_id] = result

        if self._use_celery and task_name in self._handlers:
            try:
                self.app.send_task(task_name, args=args, kwargs=kwargs, task_id=task_id)
                result.status = TaskStatus.RUNNING
            except Exception as e:
                result.status = TaskStatus.FAILURE
                result.error = str(e)
        elif task_name in self._handlers:
            result.status = TaskStatus.RUNNING
            try:
                output = self._handlers[task_name](*args, **kwargs)
                result.result = output
                result.status = TaskStatus.SUCCESS
                result.completed_at = datetime.now(UTC).isoformat()
            except Exception as e:
                result.status = TaskStatus.FAILURE
                result.error = str(e)
                result.completed_at = datetime.now(UTC).isoformat()
        else:
            result.status = TaskStatus.FAILURE
            result.error = f"Unknown task: {task_name}"

        return result

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        return self._tasks.get(task_id)

    def revoke(self, task_id: str) -> bool:
        result = self._tasks.get(task_id)
        if result and result.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            result.status = TaskStatus.REVOKED
            return True
        return False

    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[TaskResult]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks


# --- Phase 9.2: PostgreSQL Migration Runner ---


@dataclass
class Migration:
    version: str
    name: str
    up_sql: str
    down_sql: str
    checksum: str = ""

    def __post_init__(self):
        if not self.checksum:
            self.checksum = hashlib.sha256(self.up_sql.encode()).hexdigest()[:8]


class MigrationRunner:
    """Tracks and applies database migrations."""

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or os.getenv("VIORIS_DB_URL", "postgresql://localhost:5432/vioris")
        self.migrations_dir = Path("migrations")
        self._applied: dict[str, Migration] = {}
        self._pending: list[Migration] = []
        self._conn = None

    def load_migrations(self) -> list[Migration]:
        migrations = []
        if not self.migrations_dir.exists():
            return migrations

        for f in sorted(self.migrations_dir.glob("*.sql")):
            parts = f.stem.split("_", 1)
            if len(parts) == 2:
                version, name = parts
                content = f.read_text()
                up = content.split("-- DOWN")[0].strip() if "-- DOWN" in content else content.strip()
                down = content.split("-- DOWN")[1].strip() if "-- DOWN" in content else ""
                migrations.append(Migration(version=version, name=name, up_sql=up, down_sql=down))
        return migrations

    def get_applied(self) -> dict[str, str]:
        return {m.version: m.name for m in self._applied.values()}

    def get_pending(self) -> list[Migration]:
        applied_versions = set(self._applied.keys())
        return [m for m in self.load_migrations() if m.version not in applied_versions]

    def apply(self, migration: Migration) -> bool:
        try:
            self._applied[migration.version] = migration
            return True
        except Exception:
            return False

    def rollback(self, migration: Migration) -> bool:
        if migration.version in self._applied:
            del self._applied[migration.version]
            return True
        return False

    def current_version(self) -> Optional[str]:
        if not self._applied:
            return None
        return max(self._applied.keys())


# --- Phase 9.3: Tailscale Integration ---


@dataclass
class TailscalePeer:
    hostname: str
    ip: str
    online: bool = True
    last_seen: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class TailscaleManager:
    """Manages Tailscale mesh network for device-to-device access."""

    def __init__(self, tailscale_path: Optional[str] = None):
        self.tailscale_path = tailscale_path or "tailscale"
        self._peers: dict[str, TailscalePeer] = {}

    def status(self) -> dict:
        try:
            result = subprocess.run(
                [self.tailscale_path, "status", "--json"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                self._parse_status(data)
                return {"connected": True, "peers": len(self._peers)}
            return {"connected": False, "error": result.stderr.strip()}
        except FileNotFoundError:
            return {"connected": False, "error": "tailscale not installed"}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    def _parse_status(self, data: dict):
        self._peers.clear()
        peers = data.get("Peer", {})
        for peer_id, peer_data in peers.items():
            self._peers[peer_id] = TailscalePeer(
                hostname=peer_data.get("HostName", ""),
                ip=peer_data.get("TailscaleIPs", [""])[0] if peer_data.get("TailscaleIPs") else "",
                online=peer_data.get("Online", False),
            )

    def get_peers(self) -> list[TailscalePeer]:
        return list(self._peers.values())

    def find_peer(self, hostname: str) -> Optional[TailscalePeer]:
        for peer in self._peers.values():
            if peer.hostname == hostname:
                return peer
        return None

    def is_reachable(self, hostname: str) -> bool:
        peer = self.find_peer(hostname)
        return peer is not None and peer.online


# --- Phase 9.4: Hosted LLM Fallbacks ---


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    tokens_used: int = 0
    latency_ms: float = 0
    cached: bool = False


class LLMRouter:
    """Routes requests to local Ollama or hosted fallbacks."""

    PROVIDERS = {
        "ollama": {"base_url": "http://localhost:11434", "priority": 0},
        "groq": {"api_key_env": "GROQ_API_KEY", "priority": 1},
        "openrouter": {"api_key_env": "OPENROUTER_API_KEY", "priority": 2},
        "gemini": {"api_key_env": "GEMINI_API_KEY", "priority": 3},
    }

    def __init__(self, preferred_provider: Optional[str] = None):
        self.preferred_provider = preferred_provider or "ollama"
        self._provider_status: dict[str, bool] = {p: True for p in self.PROVIDERS}
        self._fallback_chain = sorted(self.PROVIDERS.keys(), key=lambda p: self.PROVIDERS[p]["priority"])

    def set_provider_status(self, provider: str, available: bool):
        self._provider_status[provider] = available

    def select_provider(self, requested: Optional[str] = None) -> str:
        provider = requested or self.preferred_provider
        if self._provider_status.get(provider, False):
            return provider

        for fallback in self._fallback_chain:
            if self._provider_status.get(fallback, False):
                return fallback
        return "ollama"

    def is_local(self, provider: Optional[str] = None) -> bool:
        return (provider or self.preferred_provider) == "ollama"

    def get_config(self, provider: str) -> dict:
        cfg = self.PROVIDERS.get(provider, {})
        if "api_key_env" in cfg:
            cfg["api_key"] = os.getenv(cfg["api_key_env"], "")
        return cfg
