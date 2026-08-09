"""Knowledge service unit tests: chunking, embedding similarity, retrieval
threshold + citation, and the approved-sources gate."""

from pathlib import Path

import pytest

from services.knowledge_service.app.chunker import Chunker
from services.knowledge_service.app.embeddings import FakeEmbedder, cosine_similarity
from services.knowledge_service.app.ingest import ApprovalGate, NotApprovedError
from services.knowledge_service.app.vector_store import VectorStore


@pytest.fixture
def store(tmp_path):
    s = VectorStore(tmp_path / "k.db", embedder=FakeEmbedder(), threshold=0.1)
    yield s
    s.close()


# ── chunker ──────────────────────────────────────────────────────────────
class TestChunker:
    def test_chunk_pages_tracks_page_number(self):
        c = Chunker()
        chunks = c.chunk_pages(["first page text here. second sentence.", "second page content."])
        assert chunks
        assert chunks[0].page == 1
        assert chunks[-1].page == 2

    def test_empty_page_yields_no_chunks(self):
        assert Chunker().chunk_text("   ") == []

    def test_deterministic(self):
        c = Chunker()
        text = "One. Two. Three. Four. Five." * 20
        assert c.chunk_text(text, page=1) == c.chunk_text(text, page=1)

    def test_overlap_preserved_on_long_text(self):
        c = Chunker(max_chars=200, overlap=40)
        text = " ".join(f"word{i}" for i in range(80))
        chunks = c.chunk_text(text)
        assert len(chunks) > 1
        first = chunks[0].text
        second = chunks[1].text
        if chunks[1].start < len(first):
            assert first[-20:] in second  # overlap tail carried over


# ── embedder ─────────────────────────────────────────────────────────────
class TestEmbedder:
    def test_fake_similarity_correlates_with_vocabulary(self):
        e = FakeEmbedder()
        assert cosine_similarity(e.embed("the cat sat on the mat"), e.embed("a cat on the mat")) > 0.5
        assert cosine_similarity(e.embed("the cat sat on the mat"), e.embed("quantum physics")) < 0.2

    def test_dimension_mismatch_raises(self):
        e = FakeEmbedder(dim=8)
        with pytest.raises(ValueError):
            cosine_similarity(e.embed("x"), [0.0] * 7)


# ── ingestion gate ───────────────────────────────────────────────────────
class TestApprovalGate:
    def test_no_roots_denies_everything(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("x")
        gate = ApprovalGate(approved_roots=[])
        with pytest.raises(NotApprovedError):
            gate.approve(f)

    def test_outside_root_denied(self, tmp_path):
        gate = ApprovalGate(approved_roots=[str(tmp_path)])
        outside = tmp_path.parent / "secret.md"
        outside.write_text("s")
        with pytest.raises(NotApprovedError):
            gate.approve(outside)

    def test_inside_root_approved(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text("hello")
        gate = ApprovalGate(approved_roots=[str(tmp_path)])
        assert gate.approve(f) == Path(f).resolve()


# ── store + retrieval ────────────────────────────────────────────────────
class TestVectorStore:
    def test_index_and_query_cites_source(self, store):
        store.index(
            "Project notes",
            ["Decided to use FastAPI for the backend. Chose local Whisper for STT."],
            source_path="notes/decisions.md",
            source_type="md",
        )
        out = store.query("what backend framework was chosen?")
        assert out.found is True
        assert "Algorithm notes" in out.citation or "notes/decisions.md" in (out.chunks[0].source_path if out.chunks else "")
        assert out.chunks and out.chunks[0].doc_id

    def test_below_threshold_returns_not_found_not_guess(self, store):
        store.index(
            "Cookbook",
            "Preheat oven to 350. Bake bread for forty minutes.",
            source_path="recipes/bread.md",
            source_type="md",
        )
        out = store.query("who won world war one?")
        assert out.found is False
        assert out.below_threshold is True
        assert "Not found in your indexed material" in out.answer

    def test_chunk_citation_carries_page(self, store):
        store.index(
            "Manual",
            ["Setup guide page content here."],
            source_path="docs/manual.txt",
            source_type="txt",
        )
        out = store.query("setup guide")
        assert out.citation is not None
        assert out.chunks and out.chunks[0].page >= 1

    def test_documents_listing(self, store):
        store.index("A", "content about fish.", "a.txt", "txt")
        docs = store.documents()
        assert len(docs) == 1 and docs[0]["title"] == "A"