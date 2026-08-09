"""
Vioris filesystem + shell guardrails (Phase 3, Prompt 3.3).

The allowlist is policy, configured through environment variables, and
DEFAULTS TO EMPTY. An empty allowlist means nothing is readable, movable,
renamable, deletable, or executable until the user grants specific paths
and programs. The guardrail layer refuses rather than attempts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _split_list(key: str) -> list[str]:
    raw = os.getenv(key, "")
    if not raw:
        return []
    sep = ";" if os.pathsep == ";" else ":"
    return [item.strip() for item in raw.split(sep) if item.strip()]


@dataclass(frozen=True)
class GuardrailConfig:
    # Absolute directory roots the file tools may touch. Empty = deny all.
    allowed_dirs: tuple[Path, ...] = ()
    # Bare program names the shell tool may run. Empty = refuse everything.
    shell_programs: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "GuardrailConfig":
        dirs = tuple(
            Path(item).expanduser().resolve() for item in _split_list("VIORUS_ALLOWED_DIRS")
        )
        programs = tuple(
            item.lower()
            for item in _split_list("VIORUS_SHELL_PROGRAMS")
        )
        return cls(allowed_dirs=dirs, shell_programs=programs)

    @property
    def has_file_access(self) -> bool:
        return bool(self.allowed_dirs)

    @property
    def has_shell_access(self) -> bool:
        return bool(self.shell_programs)


def path_is_allowed(roots: tuple[Path, ...], candidate: str | Path) -> bool:
    """True only if candidate resolves strictly inside one of the roots.

    Uses string containment on resolved absolute paths, so a root like
    C:\\Users\\me is not treated as containing C:\\Users\\meevil.
    """
    if not roots:
        return False
    try:
        resolved = Path(candidate).expanduser().resolve()
    except OSError:
        return False
    candidate_str = str(resolved).casefold()
    for root in roots:
        root_str = str(root).casefold()
        if candidate_str == root_str or candidate_str.startswith(root_str + os.sep):
            return True
    return False