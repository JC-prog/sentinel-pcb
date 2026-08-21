"""Unit tests for Case Context retrieval (app.agents.explainability_review.case_context) - the
RAG tool Explainability & Review calls. Uses hand-crafted embedding vectors (not the real
sentence-transformers model) so cosine-distance ordering is exactly known, and a fake
EmbeddingProvider so no model download/DB-free-except-Postgres is required.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.explainability_review import case_context as case_context_module
from app.agents.explainability_review.case_context import retrieve_context
from app.db.models import EMBEDDING_DIM, RemediationDoc, Workflow


def _vector(*nonzero: tuple[int, float]) -> list[float]:
    """A 384-dim vector with the given (index, value) pairs set, zero elsewhere - lets each test
    control cosine distance directly instead of depending on real embedding semantics.
    """
    vec = [0.0] * EMBEDDING_DIM
    for index, value in nonzero:
        vec[index] = value
    return vec


class _FakeEmbeddingProvider:
    def __init__(self, query_vector: list[float]) -> None:
        self._query_vector = query_vector

    def embed(self, text: str) -> list[float]:
        return self._query_vector


def _use_fake_provider(monkeypatch: pytest.MonkeyPatch, query_vector: list[float]) -> None:
    monkeypatch.setattr(
        case_context_module,
        "get_embedding_provider",
        lambda: _FakeEmbeddingProvider(query_vector),
    )


def _remediation_doc(defect_label: str, title: str, embedding: list[float]) -> RemediationDoc:
    return RemediationDoc(
        defect_label=defect_label,
        title=title,
        content=f"How to handle {defect_label}",
        embedding=embedding,
    )


def _workflow(
    workflow_id: str, embedding: list[float] | None, defect_label: str = "Solder Bridge"
) -> Workflow:
    return Workflow(
        id=workflow_id,
        image_filename="x.png",
        image_path="/tmp/x.png",
        defect_label=defect_label,
        decision="auto_accept",
        embedding=embedding,
    )


@pytest_asyncio.fixture
async def add_remediation_doc(
    db_session: AsyncSession,
) -> AsyncGenerator[Callable[[str, str, list[float]], Awaitable[RemediationDoc]], None]:
    """`remediation_docs` is deliberately excluded from conftest's post-test truncation (it's
    reference/seed data, per its comment), so tests that add rows to it must delete their own
    rows afterward instead of relying on the table starting empty.
    """

    created: list[RemediationDoc] = []

    async def _add(defect_label: str, title: str, embedding: list[float]) -> RemediationDoc:
        doc = _remediation_doc(defect_label, title, embedding)
        db_session.add(doc)
        await db_session.flush()
        created.append(doc)
        return doc

    yield _add

    for doc in created:
        await db_session.delete(doc)
    await db_session.commit()


async def test_guidance_ranked_by_cosine_distance_and_limited_to_k(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    add_remediation_doc: Callable[[str, str, list[float]], Awaitable[RemediationDoc]],
) -> None:
    _use_fake_provider(monkeypatch, _vector((0, 1.0)))

    await add_remediation_doc("Solder Bridge", "Closest", _vector((0, 1.0)))
    await add_remediation_doc("Misalignment", "Middle", _vector((0, 0.7), (1, 0.3)))
    await add_remediation_doc("Tombstone", "Farthest", _vector((1, 1.0)))
    await db_session.commit()

    context = await retrieve_context(db_session, "query text", current_workflow_id="none", k=2)

    assert [doc["title"] for doc in context.guidance] == ["Closest", "Middle"]


async def test_precedents_exclude_current_workflow(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_provider(monkeypatch, _vector((0, 1.0)))

    current = _workflow("current-id", embedding=_vector((0, 1.0)))
    other = _workflow("other-id", embedding=_vector((0, 1.0)))
    db_session.add_all([current, other])
    await db_session.commit()

    context = await retrieve_context(
        db_session, "query text", current_workflow_id="current-id", k=5
    )

    assert [p["workflow_id"] for p in context.precedents] == ["other-id"]


async def test_precedents_exclude_workflows_without_embedding(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake_provider(monkeypatch, _vector((0, 1.0)))

    has_embedding = _workflow("has-embedding", embedding=_vector((0, 1.0)))
    no_embedding = _workflow("no-embedding", embedding=None)
    db_session.add_all([has_embedding, no_embedding])
    await db_session.commit()

    context = await retrieve_context(
        db_session, "query text", current_workflow_id="unrelated", k=5
    )

    assert [p["workflow_id"] for p in context.precedents] == ["has-embedding"]


async def test_guidance_and_precedent_dict_shapes(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    add_remediation_doc: Callable[[str, str, list[float]], Awaitable[RemediationDoc]],
) -> None:
    _use_fake_provider(monkeypatch, _vector((0, 1.0)))

    await add_remediation_doc("Solder Bridge", "Fix solder bridges", _vector((0, 1.0)))
    workflow = _workflow("wf-1", embedding=_vector((0, 1.0)), defect_label="Solder Bridge")
    workflow.board_id = "board-1"
    workflow.component_id = "comp-1"
    db_session.add(workflow)
    await db_session.commit()

    context = await retrieve_context(
        db_session, "query text", current_workflow_id="unrelated", k=5
    )

    assert context.guidance[0] == {
        "title": "Fix solder bridges",
        "defect_label": "Solder Bridge",
        "content": "How to handle Solder Bridge",
    }
    assert context.precedents[0] == {
        "workflow_id": "wf-1",
        "board_id": "board-1",
        "component_id": "comp-1",
        "defect_label": "Solder Bridge",
        "decision": "auto_accept",
    }


async def test_no_workflows_returns_empty_precedents(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike `remediation_docs`, `workflows` *is* truncated between tests (conftest.py), so this
    is the one case where an empty-table assertion is reliable.
    """
    _use_fake_provider(monkeypatch, _vector((0, 1.0)))

    context = await retrieve_context(db_session, "query text", current_workflow_id="none", k=3)

    assert context.precedents == []
