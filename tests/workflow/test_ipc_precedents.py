"""Agent 2's precedent search: the ported reader (src/data/qdrant_store.py, adapted to this app's
Qdrant and OpenAI embeddings) and the ingest script that fills what it reads. Qdrant is in-memory and
the embeddings are a tiny deterministic stand-in, so nothing touches a network."""

import importlib
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient

from app.shared.config.settings import settings
from app.workflow.services import agent2_mcp_service, ipc_ingest

store = importlib.import_module("app.workflow.src.data.qdrant_store")


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Vectors that put 'tombstone' text near a tombstone query and 'shift' text near a shift one."""

    def one(text: str) -> list[float]:
        t = text.lower()
        vec = [0.0] * store.VECTOR_DIM
        vec[0] = 1.0 if "tombstone" in t else 0.0
        vec[1] = 1.0 if "shift" in t or "overhang" in t else 0.0
        vec[2] = 0.05  # a little of everything, so no vector is all zeros
        return vec

    return [one(t) for t in texts]


@pytest.fixture
def qdrant(monkeypatch: pytest.MonkeyPatch) -> QdrantClient:
    client = QdrantClient(":memory:")
    monkeypatch.setattr(store, "_client", client)
    monkeypatch.setattr(store, "embed_texts", fake_embed)
    monkeypatch.setattr(settings, "ipc_precedent_min_score", 0.3)
    return client


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


# --- the reader ---------------------------------------------------------------------------------


def test_a_missing_collection_is_no_precedents_not_an_error(qdrant: QdrantClient) -> None:
    assert store.query_defect_precedents("tombstone causes") == []


def test_it_returns_the_nearest_chunks_with_their_source_and_page(
    qdrant: QdrantClient, tmp_path: Path
) -> None:
    _write(
        tmp_path,
        "ipc.txt",
        "Tombstone: a component detached at one end.\n\nShifted: side overhang above fifty percent.",
    )
    ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)

    hits = store.query_defect_precedents("what causes a tombstone", top_k=1)

    assert len(hits) == 1
    assert "detached at one end" in hits[0]["text"]
    assert hits[0]["source"] == "ipc.txt" and hits[0]["page"] is None
    assert 0.3 <= hits[0]["score"] <= 1.0


def test_a_chunk_below_the_minimum_score_is_never_cited(
    qdrant: QdrantClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "ipc.txt", "Shifted: side overhang above fifty percent.")
    ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)
    monkeypatch.setattr(settings, "ipc_precedent_min_score", 0.99)

    assert store.query_defect_precedents("tombstone") == []


def test_without_an_embedding_it_finds_nothing_and_never_invents_a_vector(
    qdrant: QdrantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_key(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no OpenAI API key is configured")

    monkeypatch.setattr(store, "embed_texts", no_key)

    assert store.query_defect_precedents("tombstone") == []


def test_embeddings_need_a_key_and_a_successful_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(type(settings), "orchestrator_api_key", property(lambda self: ""))

    with pytest.raises(RuntimeError, match="no OpenAI API key"):
        store.embed_texts(["x"])


# --- the ingest script --------------------------------------------------------------------------


def test_chunking_keeps_short_text_whole_and_splits_long_text_with_overlap() -> None:
    assert ipc_ingest.chunk_text("A short clause.") == ["A short clause."]

    long = " ".join(f"word{i}" for i in range(600))
    chunks = ipc_ingest.chunk_text(long, size=500, overlap=80)

    assert len(chunks) > 1 and all(len(c) <= 600 for c in chunks)
    # the end of one chunk reappears at the start of the next, so a clause is never cut in two
    assert chunks[0].split()[-1] in chunks[1]


def test_paragraphs_are_packed_into_chunks_not_one_each() -> None:
    text = "\n\n".join(f"Paragraph {i} about a defect." for i in range(5))

    chunks = ipc_ingest.chunk_text(text, size=1000)

    assert len(chunks) == 1 and "Paragraph 4" in chunks[0]


def test_ingesting_the_same_files_twice_changes_nothing(qdrant: QdrantClient, tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "Tombstone criteria.\n\nShift criteria.")
    ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)
    first = qdrant.count(store.COLLECTION_NAME).count

    ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)

    assert qdrant.count(store.COLLECTION_NAME).count == first > 0


def test_replace_drops_what_a_file_contributed_before(qdrant: QdrantClient, tmp_path: Path) -> None:
    padding = " filler" * 100  # long enough that the two paragraphs are two chunks
    path = _write(
        tmp_path, "a.txt", f"Shifted criteria.{padding}\n\nTombstone criteria.{padding}"
    )
    first = ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)
    path.write_text("Shifted criteria only now.")  # the file got shorter

    ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)
    left_behind = store.query_defect_precedents("tombstone", top_k=5)
    ipc_ingest.ingest(tmp_path, replace=True, client=qdrant, embed=fake_embed)
    after = store.query_defect_precedents("tombstone", top_k=5)

    assert first == {"a.txt": 2}
    assert any("Tombstone criteria" in h["text"] for h in left_behind)  # stale without --replace
    assert not any("Tombstone criteria" in h["text"] for h in after)
    assert qdrant.count(store.COLLECTION_NAME).count == 1


def test_a_pdf_is_read_page_by_page_so_a_hit_can_cite_its_page(
    qdrant: QdrantClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Page:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class Reader:
        def __init__(self, path: str) -> None:
            self.pages = [Page("Cover page."), Page("Tombstone: lifted termination, page two.")]

    monkeypatch.setattr("pypdf.PdfReader", Reader)
    (tmp_path / "ipc-a-610.pdf").write_bytes(b"%PDF-1.4")

    ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)
    hits = store.query_defect_precedents("tombstone", top_k=1)

    assert hits[0]["source"] == "ipc-a-610.pdf" and hits[0]["page"] == 2


def test_a_directory_with_nothing_readable_is_refused(qdrant: QdrantClient, tmp_path: Path) -> None:
    (tmp_path / "notes.docx").write_bytes(b"x")

    with pytest.raises(ValueError, match="no .txt, .md or .pdf"):
        ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)


def test_unsupported_files_in_a_directory_are_skipped(qdrant: QdrantClient, tmp_path: Path) -> None:
    _write(tmp_path, "ok.txt", "Tombstone criteria.")
    (tmp_path / "skip.docx").write_bytes(b"x")

    stored = ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)

    assert stored == {"ok.txt": 1}


# --- the explain service now gets them ----------------------------------------------------------


def test_the_explain_service_gets_precedents_through_the_ported_tool(
    qdrant: QdrantClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "ipc.txt", "Tombstone: a component detached at one end.")
    ipc_ingest.ingest(tmp_path, client=qdrant, embed=fake_embed)
    tools = agent2_mcp_service.load_ported_tools()
    # the ported MCP tool imports the reader by its bare name (src.data...), a second module object
    # next to the dotted one used above - point that one at the same in-memory Qdrant too
    bare = importlib.import_module("src.data.qdrant_store")
    monkeypatch.setattr(bare, "_client", qdrant)
    monkeypatch.setattr(bare, "embed_texts", fake_embed)

    result: dict[str, Any] = tools.precedents(
        component_ref="C12", board_id="B1", issue_symptom="tombstone on one end"
    )

    assert result["similar_cases"] and result["similar_cases"][0]["source"] == "ipc.txt"
    assert "[ipc.txt]" in result["ipc_standard"] and "detached at one end" in result["ipc_standard"]
