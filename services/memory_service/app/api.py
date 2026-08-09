"""
Memory Service API (Phase 4, Prompt 4.1).

FastAPI surface for storing, listing, correcting, and deleting memories.

Run:
    uvicorn services.memory_service.app.api:app --port 8441
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from packages.shared.schemas import Memory, MemoryCategory

from .store import MemoryStore

app = FastAPI(title="Vioris Memory Service")

_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        from pathlib import Path

        _store = MemoryStore(Path("vioris_data/memory.db"))
    return _store


class MemoryCreateRequest(BaseModel):
    category: MemoryCategory
    content: str = Field(min_length=1)
    source: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    user_opted_in: bool = False  # must be True for sensitive-category writes


class MemoryCorrectRequest(BaseModel):
    content: str = Field(min_length=1)
    category: MemoryCategory | None = None  # re-categorize if provided


@app.get("/memory")
def list_memory(category: MemoryCategory | None = None) -> list[dict]:
    return [_mem_dict(m) for m in get_store().list(category)]


@app.post("/memory")
def create_memory(req: MemoryCreateRequest) -> dict:
    store = get_store()
    if req.category == MemoryCategory.SENSITIVE and not req.user_opted_in:
        raise HTTPException(status_code=409, detail="sensitive category requires user_opted_in=true")
    result = store.store_and_user_opt_in(
        req.category, req.content, req.source, req.confidence, req.user_opted_in
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return _mem_dict(result.memory)


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
        "content": mem.content,
        "source": mem.source,
        "confidence": mem.confidence,
        "user_opted_in": mem.user_opted_in,
        "created_at": mem.created_at.isoformat(),
        "updated_at": mem.updated_at.isoformat(),
    }