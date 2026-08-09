"""Knowledge service integration: HTTP surface, approval gate, retrieval."""

import pytest
from fastapi.testclient import TestClient

from services.knowledge_service.app.api import app
from services.knowledge_service.app.embeddings import FakeEmbedder
from services.knowledge_service.app.vector_store import VectorStore

APPROVED = "approved.md"
OUTSIDE = "outside.md"


@pytest.fixture
def client(tmp_path):
    from services.knowledge_service.app import api as knowledge_api

    approved_src = tmp_path / "approved.md"
    approved_src.write_text("Decided on local Whisper for speech and FastAPI for APIs.")
    (tmp_path / APPROVED).write_text("Decided to use local Whisper for speech. FastAPI runs the API layer.")

    knowledge_api._store = VectorStore(tmp_path / "k.db", embedder=FakeEmbedder(), threshold=0.1)
    knowledge_api._gate = knowledge_api.ApprovalGate([])
    approved = knowledge_api.ApprovalGate([str(tmp_path)])
    knowledge_api._gate = approved
    with TestClient(app) as c:
        c._approved = str(approved_src)
        c._tmp = tmp_path
        yield c


def test_ingest_and_query_with_citation(client):
    r = client.post("/knowledge/ingest", json={"title": "Project decisions", "path": client._approved})
    assert r.status_code == 200
    assert r.json()["chunks_indexed"] >= 1

    q = client.post("/knowledge/query", json={"question": "what speech model was decided on?"}).json()
    assert q["found"] is True
    assert "Project decisions" in (q["citation"] or "")
    assert q["sources"]


def test_ingest_from_outside_approved_sources_is_forbidden(client):
    bad = client._tmp.parent / "sensitive.md"
    bad.write_text("classification: internal")
    r = client.post("/knowledge/ingest", json={"title": "bad", "path": str(bad)})
    assert r.status_code == 403
    assert "not approved" in r.json()["detail"].lower()


def test_no_approved_roots_denies_all(client):
    from services.knowledge_service.app import api as knowledge_api

    knowledge_api._gate = knowledge_api.ApprovalGate([])
    inner = client._tmp / "approved.md"
    r = client.post("/knowledge/ingest", json={"title": "x", "path": str(inner)})
    assert r.status_code == 403


def test_query_below_threshold_reports_not_found(client):
    client.post("/knowledge/ingest", json={"title": "notes", "path": client._approved})
    q = client.post("/knowledge/query", json={"question": "quantum entanglement of pasta"}).json()
    assert q["found"] is False
    assert q["below_threshold"] is True
    assert "Not found in your indexed material" in q["answer"]


def test_documents_endpoint(client):
    client.post("/knowledge/ingest", json={"title": "notes", "path": client._approved})
    docs = client.get("/knowledge/documents").json()
    assert len(docs) == 1 and docs[0]["title"] == "notes"