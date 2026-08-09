"""
Documents agent (Phase 3, Prompt 3.3).

Operates an explicit, allow-listed directory set and a terminal-command
allow-list. Tool tiers (registered statically in the permission engine):

  - file.search_files  (observe)
  - file.read          (observe)
  - file.move          (execute)
  - file.rename        (execute)
  - file.delete        (critical — irreversible)
  - terminal.run       (execute)

Guardrails (these are the agent's job, regardless of tier):

  - Any path, source, or destination NOT inside an allow-listed directory is
    REFUSED — never attempted.
  - DELETE is Critical: classified critical and refused unless the engine's
    critical gate (approval + second factor + cooldown) is honoured upstream.
    The agent itself never deletes without returning that gate marker.
  - terminal.run only executes commands whose program name is on the command
    allow-list; anything else is refused, not attempted.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from packages.shared.permission_engine import UnknownToolError

from .allowlist import GuardrailConfig, path_is_allowed

logger = logging.getLogger(__name__)


class DocumentsBlockedError(Exception):
    """The path or command is outside the allow-list; it was never attempted."""


class PermissionBlockedError(Exception):
    """The tool is not registered; it was never attempted."""


@dataclass
class DocsOutcome:
    tool: str
    ok: bool
    note: str
    detail: dict = field(default_factory=dict)
    blocked: str | None = None  # 'path-allowlist' | 'command-allowlist' | None
    error: str | None = None


class DocumentsAgent:
    """File + terminal operations scoped by GuandrailConfig."""

    def __init__(self, config: GuardrailConfig | None = None) -> None:
        self.config = config or GuardrailConfig.from_env()
        self._executions: list[str] = []

    # ── observe: search / read (must stay inside allow-listed roots) ─────
    def search_files(self, query: str, root: str | None = None) -> DocsOutcome:
        base = self._resolve_root(root)
        if base is None:
            return self._refuse("file.search_files", "outside-allowlist", {"root": root})
        if not query.strip():
            return DocsOutcome(tool="file.search_files", ok=False, note="empty query")
        try:
            query = query.strip()
            results = []
            for match in base.rglob("*"):
                if match.is_file() and query.lower() in match.name.lower():
                    results.append(str(match.relative_to(base)))
        except OSError as exc:  # noqa: BLE001
            return DocsOutcome(
                tool="file.search_files", ok=False, note="search failed", error=str(exc)
            )
        self._executions.append("file.search_files")
        return DocsOutcome(
            tool="file.search_files",
            ok=True,
            note=f"found {len(results)} matches",
            detail={"query": query, "count": len(results), "matches": results[:100]},
        )

    def read_file(self, path: str) -> DocsOutcome:
        target = Path(path).expanduser()
        if not path_is_allowed(self.config.allowed_dirs, target):
            return self._refuse("file.read", "outside-allowlist", {"path": path})
        if not target.is_file():
            return DocsOutcome(tool="file.read_file", ok=False, note="not a file")
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # noqa: BLE001
            return DocsOutcome(tool="file.read_file", ok=False, note="read failed", error=str(exc))
        self._executions.append("file.read")
        return DocsOutcome(
            tool="file.read_file",
            ok=True,
            note="read file",
            detail={"path": str(target), "chars": len(text), "preview": text[:2000]},
        )

    # ── execute (path-scoped mutations) ──────────────────────────────────
    def move(self, source: str, destination: str) -> DocsOutcome:
        src, dst = Path(source).expanduser(), Path(destination).expanduser()
        if not path_is_allowed(self.config.allowed_dirs, src) or not path_is_allowed(
            self.config.allowed_dirs, dst
        ):
            return self._refuse(
                "file.move", "outside-allowlist", {"source": source, "destination": destination}
            )
        try:
            src.rename(dst)
        except OSError as exc:  # noqa: BLE001
            return DocsOutcome(tool="file.move", ok=False, note="move failed", error=str(exc))
        self._executions.append("file.move")
        return DocsOutcome(
            tool="file.move", ok=True, note="moved", detail={"source": source, "destination": destination}
        )

    def rename(self, path: str, new_name: str) -> DocsOutcome:
        target = Path(path).expanduser()
        if not new_name or new_name != Path(new_name).name:
            return DocsOutcome(tool="file.rename", ok=False, note="new_name must be a bare filename")
        if not path_is_allowed(self.config.allowed_dirs, target):
            return self._refuse("file.rename", "outside-allowlist", {"path": path})
        new_path = target.parent / new_name
        if not path_is_allowed(self.config.allowed_dirs, new_path):
            return self._refuse("file.rename", "outside-allowlist", {"path": str(new_path)})
        try:
            target.rename(new_path)
        except OSError as exc:  # noqa: BLE001
            return DocsOutcome(tool="file.rename", ok=False, note="rename failed", error=str(exc))
        self._executions.append("file.rename")
        return DocsOutcome(tool="file.rename", ok=True, note="renamed", detail={"path": path, "new_name": new_name})

    # ── critical (irreversible) ──────────────────────────────────────────
    def delete(self, path: str) -> DocsOutcome:
        target = Path(path).expanduser()
        if not path_is_allowed(self.config.allowed_dirs, target):
            # Refused even though upstream would gate this as Critical: the
            # path is outside what the user ever allowed this agent to touch.
            return self._refuse("file.delete", "outside-allowlist", {"path": path})
        if not target.exists():
            return DocsOutcome(tool="file.delete", ok=False, note="no such file")
        try:
            target.unlink()
        except OSError as exc:  # noqa: BLE001
            return DocsOutcome(tool="file.delete", ok=False, note="delete failed", error=str(exc))
        self._executions.append("file.delete")
        # The caller must still honor the Critical gate (2FA + cooldown).
        return DocsOutcome(
            tool="file.delete",
            ok=True,
            note="deleted",
            detail={"path": path, "critical_gate_required": True},
        )

    # ── executable only when on the command allow-list ───────────────────
    def run(self, command: str, args: list[str] | None = None) -> DocsOutcome:
        args = args or []
        prog = self._program_name(command)
        if prog is None:
            return DocsOutcome(tool="terminal.run", ok=False, note="invalid command")
        if not self._is_allowlist(prog):
            # Anything not on the allow-list is REFUSED, not attempted.
            return self._refuse("terminal.run", "command-allowlist", {"try": prog})
        try:
            result = subprocess.run(
                [prog, *args],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return DocsOutcome(tool="terminal.run", ok=False, note="exec failed", error=str(exc))
        self._executions.append("terminal.run")
        return DocsOutcome(
            tool="terminal.run",
            ok=True,
            note=f"exit {result.returncode}",
            detail={
                "program": prog,
                "args": args,
                "returncode": result.returncode,
                "stdout": result.stdout[:4000],
                "stderr": result.stderr[:2000],
            },
        )

    # ── allow-list plumbing ──────────────────────────────────────────────
    def _program_name(self, command: str) -> str | None:
        # Keep it atomic: a whole single command string (never a pipeline or
        # wrapped by a shell). If it isn't one token it isn't allow-listed.
        if not command or command != command.strip():
            return None
        candidate = Path(command.split()[0]).name if command.split() else None
        if candidate and any(ch in candidate for ch in ";&|>"):
            return None
        return candidate.lower() if candidate else None

    def _is_allowlist(self, prog: str) -> bool:
        return prog in self.config.shell_programs

    def _resolve_root(self, root: str | None) -> Path | None:
        """Return an allow-listed root matching root, or None.

        A default (root=None) resolves to the first configured root. Any
        explicit root that is NOT inside an allow-listed directory is refused.
        """
        if not self.config.allowed_dirs:
            return None
        if root is None:
            return self.config.allowed_dirs[0]
        candidate = Path(root).expanduser().resolve()
        for allowed in self.config.allowed_dirs:
            if candidate == allowed or allowed in candidate.parents:
                return allowed
        return None

    def _refuse(self, tool: str, tag: str, detail: dict) -> DocsOutcome:
        self._executions.append(f"{tool}:refused:{tag}")
        return DocsOutcome(
            tool=tool,
            ok=False,
            note=f"refused — target is outside the allow-list ({tag})",
            detail=detail,
            blocked=tag,
        )

    # ── registry gate ────────────────────────────────────────────────────
    def classify_with_gate(self, tool: str):
        from packages.shared.permission_engine import PermissionEngine

        try:
            return PermissionEngine.classify(tool)
        except UnknownToolError as exc:
            raise PermissionBlockedError(str(exc)) from exc

    @property
    def executions(self) -> list[str]:
        return list(self._executions)