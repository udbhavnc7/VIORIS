"""
Knowledge Service API (Phase 4, Prompt 4.2).

    POST   /knowledge/ingest    {title, path}           index an approved doc
    GET    /knowledge/documents list indexed documents
    POST   /knowledge/query     {question}              source-cited answer
    GET    /knowledge/health                           backend status

Run:
    uvicorn services.knowledge_service.app.api:app --port 8442
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .embeddings import OllamaEmbedder
from .ingest import ApprovalGate, DocumentExtractionError, NotApprovedError, extract_pages
from .vector_store import QueryResult, VectorStore

app = FastAPI(title="Vioris Knowledge Service")

_store: VectorStore | None = None
_gate: ApprovalGate | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore(Path("vioris_data/knowledge.db"), embedder=OllamaEmbedder())
    return _store


def get_gate() -> ApprovalGate:
    global _gate
    if _gate is None:
        _gate = ApprovalGate()
    return _gate


class IngestRequest(BaseModel):
    title: str
    path: str  # must resolve inside the approved sources


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = 3


@app.get("/knowledge/health")
def health() -> dict:
    return {"service": "knowledge", "embedder": type(get_store().embedder).__name__}


@app.post("/knowledge/ingest")
def ingest(req: IngestRequest) -> dict:
    src = Path(req.path)
    try:
        approved = get_gate().approve(src)
    except NotApprovedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        pages = extract_pages(approved)
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if pages is None:
        raise HTTPException(
            status_code=422, detail=f"unsupported document type: {src.suffix or '<none>'}"
        )
    result = get_store().index(req.title, pages, str(src), src.suffix.lower().lstrip(".") or "txt")
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error)
    return {
        "doc_id": result.doc_id,
        "title": req.title,
        "chunks_indexed": result.chunks_indexed,
        "pages": len(pages),
    }


@app.get("/knowledge/documents")
def documents() -> list[dict]:
    return get_store().documents()


@app.post("/knowledge/query")
def query(req: QueryRequest) -> dict:
    return _query_dict(get_store().query(req.question, top_k=req.top_k))


def _query_dict(result: QueryResult) -> dict:
    payload = {
        "answer": result.answer,
        "found": result.found,
        "citation": result.citation,
        "below_threshold": result.below_threshold,
        "detail": result.detail,
    }
    payload["sources"] = [
        {
            "title": c.title,
            "source_path": c.source_path,
            "page": c.page,
            "score": round(c.score, 4),
            "start_char": c.start_char,
            "end_char": c.end_char,
        }
        for c in result.chunks
    ]
    return payload