"""
Document extraction + approval gate (Phase 4, Prompt 4.2).

Only documents inside an approved-sources directory may be ingested and
indexed. Extraction is explicit and proven: markdown/notes/txt are read as
text; PDFs are extracted with pypdf (a later phase adds OCR for scans).
Nothing is ever ingested from a path that is not approved.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".note", ".rst")
PDF_EXTENSION = ".pdf"


class ApprovalGate:
    """Exactly-instant allow-list: a source path is valid iff inside a root."""

    def __init__(self, approved_roots: list[str] | None = None) -> None:
        roots = approved_roots if approved_roots is not None else _env_roots()
        self.roots = [Path(r).expanduser().resolve() for r in roots if r.strip()]

    @property
    def empty(self) -> bool:
        return not self.roots

    def approve(self, source: str | Path) -> Path:
        """Raise NotApprovedError unless source resolves inside a root."""
        if not self.roots:
            raise NotApprovedError(source, "no approved sources are configured")
        candidate = Path(source).expanduser().resolve()
        for root in self.roots:
            try:
                candidate.relative_to(root)
                return candidate
            except ValueError:
                continue
        raise NotApprovedError(source, "outside approved sources")


def _env_roots() -> list[str]:
    raw = os.getenv("VIORUS_APPROVED_KNOWLEDGE_SOURCES", "")
    if not raw:
        return []
    sep = ";" if os.pathsep == ";" else ":"
    return [item for item in raw.split(sep) if item.strip()]


class NotApprovedError(Exception):
    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        super().__init__(f"document '{source}' is not approved for indexing: {reason}")


def extract_pages(path: Path) -> list[str] | None:
    """Return page texts for a supported document type, or None if unsupported."""
    ext = path.suffix.lower()
    if ext in TEXT_EXTENSIONS:
        text = path.read_text(encoding="utf-8", errors="replace")
        return [text]  # no real pages; retrieval cites the whole file
    if ext == PDF_EXTENSION:
        try:
            from pypdf import PdfReader  # local, optional
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise DocumentExtractionError(
                f"PDF extraction requires pypdf; install it to index PDFs ({path})"
            ) from exc
        reader = PdfReader(str(path))
        return [page.extract_text() or "" for page in reader.pages]
    return None


class DocumentExtractionError(Exception):
    pass