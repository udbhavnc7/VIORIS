"""
Memory Service API (Phase 4, Prompt 4.1).

FastAPI surface for storing, listing, correcting, and deleting memories.

Run:
    uvicorn services.memory_service.app.api:app --port 8441
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from packages.shared.memory_split import (
    EpisodicQuery,
    MemorySplitter,
    SemanticQuery,
)
from packages.shared.schemas import Memory, MemoryCategory

from .query_planner import plan_memory_query
from .store import MemoryStore

app = FastAPI(title="Vioris Memory Service")

_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        from pathlib import Path

        _store = MemoryStore(Path("vioris_data/memory.db"))
    return _store


_splitter: MemorySplitter | None = None


def get_splitter() -> MemorySplitter:
    global _splitter
    if _splitter is None or _splitter.store is not get_store():
        _splitter = MemorySplitter(store=get_store())
    return _splitter


class MemoryCreateRequest(BaseModel):
    category: MemoryCategory
    content: str = Field(min_length=1)
    source: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    user_opted_in: bool = False  # must be True for sensitive-category writes
    relationship: str = ""  # e.g. spouse, manager — used for contact resolution


class MemoryCorrectRequest(BaseModel):
    content: str = Field(min_length=1)
    category: MemoryCategory | None = None  # re-categorize if provided


class MemoryQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    keyword: str | None = None
    category: MemoryCategory | None = None
    after: datetime | None = None
    before: datetime | None = None
    limit: int = Field(default=20, ge=1, le=200)


@app.get("/memory")
def list_memory(category: MemoryCategory | None = None) -> list[dict]:
    return [_mem_dict(m) for m in get_store().list(category)]


@app.post("/memory")
def create_memory(req: MemoryCreateRequest) -> dict:
    store = get_store()
    if req.category == MemoryCategory.SENSITIVE and not req.user_opted_in:
        raise HTTPException(status_code=409, detail="sensitive category requires user_opted_in=true")
    result = store.store_and_user_opt_in(
        req.category, req.content, req.source, req.confidence, req.user_opted_in,
        relationship=req.relationship,
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return _mem_dict(result.memory)


@app.post("/memory/query")
def query_memory(req: MemoryQueryRequest) -> dict:
    """Route a question to episodic or semantic memory and return cited hits.

    The collection choice is made by the deterministic query planner, never by
    the caller or by content fetched from memory.
    """
    plan = plan_memory_query(req.query)
    splitter = get_splitter()
    if plan.memory_type.value == "episodic":
        results = splitter.query_episodic(
            EpisodicQuery(
                after=req.after,
                before=req.before,
                category=req.category,
                keyword=req.keyword,
                limit=req.limit,
            )
        )
    else:
        results = splitter.query_semantic(
            SemanticQuery(
                category=req.category,
                keyword=req.keyword,
                limit=req.limit,
            )
        )
    return {
        "memory_type": plan.memory_type.value,
        "reason": plan.reason,
        "signals": list(plan.signals),
        "count": len(results),
        "memories": [_mem_dict(m) for m in results],
    }


@app.post("/memory/correct/{memory_id}")
def correct_memory(memory_id: str, req: MemoryCorrectRequest) -> dict:
    result = get_store().correct(memory_id, req.content, category_overwrite=req.category)
    if not result.ok:
        raise HTTPException(status_code=404, detail=result.error)
    return _mem_dict(result.memory)


@app.delete("/memory/{memory_id}")
def delete_memory(memory_id: str) -> dict:
    result = get_store().delete(memory_id)
    if not result.ok:
        raise HTTPException(status_code=404, detail=result.error)
    return result.detail


def _mem_dict(mem: Memory | None) -> dict:
    if mem is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return {
        "memory_id": mem.memory_id,
        "category": mem.category.value,
        "memory_type": mem.memory_type.value if mem.memory_type else None,
        "content": mem.content,
        "source": mem.source,
        "relationship": mem.relationship,
        "confidence": mem.confidence,
        "user_opted_in": mem.user_opted_in,
        "created_at": mem.created_at.isoformat(),
        "updated_at": mem.updated_at.isoformat(),
    }