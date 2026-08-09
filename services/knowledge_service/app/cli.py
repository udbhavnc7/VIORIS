"""
Knowledge Service CLI (Phase 4, Prompt 4.2).

    python -m services.knowledge_service.app.cli ingest --title "AP" --path path/to/notes.md
    python -m services.knowledge_service.app.cli documents
    python -m services.knowledge_service.app.cli query "what did we decide about x?"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .embeddings import FakeEmbedder, OllamaEmbedder
from .ingest import ApprovalGate, NotApprovedError, extract_pages
from .vector_store import VectorStore

DEFAULT_DB = Path("vioris_data/knowledge.db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vioris-knowledge")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--fake-embedder", action="store_true", help="use deterministic local embedder (no Ollama)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="index an approved document")
    p.add_argument("--title", required=True)
    p.add_argument("path")

    sub.add_parser("documents", help="list indexed documents")

    p = sub.add_parser("query", help="ask over indexed material")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("question")

    args = parser.parse_args(argv)

    embedder = FakeEmbedder() if args.fake_embedder else OllamaEmbedder()
    store = VectorStore(args.db, embedder=embedder)

    if args.cmd == "ingest":
        src = Path(args.path)
        try:
            approved = ApprovalGate().approve(src)
            pages = extract_pages(approved)
        except (NotApprovedError, Exception) as exc:  # noqa: BLE001
            print(f"ERROR: {exc}")
            return 1
        if pages is None:
            print(f"ERROR: unsupported type {src.suffix}")
            return 1
        res = store.index(args.title, pages, str(src), src.suffix.lstrip(".") or "txt")
        if not res.ok:
            print(f"ERROR: {res.error}")
            return 1
        print(json.dumps({"doc_id": res.doc_id, "chunks_indexed": res.chunks_indexed}, indent=2))
        return 0

    if args.cmd == "documents":
        print(json.dumps(store.documents(), indent=2))
        return 0

    if args.cmd == "query":
        result = store.query(args.question, top_k=args.top_k)
        print(result.answer)
        for c in result.chunks:
            print(f"  [{round(c.score, 3)}] {c.title} (page {c.page}): {c.text[:120]}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())