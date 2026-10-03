"""Covers the one file adapted so vector-db indexing writes to this app's shared Docker Qdrant
instead of a local embedded store: app/workflow/data/qdrant_indexer.py. Imported through the same
sys.path bridge app/workflow/services/streaming.py uses at runtime (see _bridge_sys_path there) -
this module uses the teammate's own bare (unqualified) imports internally, but does import
app.shared.config.settings directly (a deliberate, documented exception - see
app/workflow/INTEGRATION_NOTES.md's "vector-db indexing" section).
"""

from typing import Any

import pytest

from app.shared.config.settings import settings
from app.workflow.services.streaming import _bridge_sys_path

_bridge_sys_path()

from data.qdrant_indexer import populate_qdrant_db  # type: ignore


class _FakeCollections:
    def __init__(self, names: list[str]) -> None:
        self.collections = [type("C", (), {"name": n})() for n in names]


class _FakeCount:
    def __init__(self, count: int) -> None:
        self.count = count


class _FakeQdrantClient:
    """Records how it was constructed and what was upserted - never touches a real Qdrant."""

    last_init_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        _FakeQdrantClient.last_init_kwargs = kwargs
        self.upserted: list[Any] = []
        self._collection_names: list[str] = []

    def get_collections(self) -> _FakeCollections:
        return _FakeCollections(self._collection_names)

    def create_collection(self, collection_name: str, **kwargs: Any) -> None:
        self._collection_names.append(collection_name)

    def upsert(self, collection_name: str, points: list[Any]) -> None:
        self.upserted.extend(points)

    def count(self, collection_name: str) -> _FakeCount:
        return _FakeCount(len(self.upserted))

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _fake_qdrant_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("data.qdrant_indexer.QdrantClient", _FakeQdrantClient)
    _FakeQdrantClient.last_init_kwargs = None


@pytest.fixture(autouse=True)
def _deterministic_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never make a real OpenAI/LiteLLM call from a unit test - get_embeddings() already has a
    deterministic offline fallback when no usable key is present; force that path."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _sample(**overrides: Any) -> dict[str, Any]:
    base = {
        "sample_id": "S1",
        "board_id": "BOARD-1",
        "component_id": "U7",
        "feature_type": "Body",
        "defect_hint": "Tombstone",
        "failed_inspections": {},
        "defect_image_path": "/tmp/defect.jpg",
        "golden_image_path": "/tmp/golden.jpg",
    }
    base.update(overrides)
    return base


def test_populate_qdrant_db_connects_to_the_shared_docker_instance() -> None:
    populate_qdrant_db([_sample()])

    assert _FakeQdrantClient.last_init_kwargs == {"url": settings.qdrant_url}


def test_populate_qdrant_db_upserts_one_point_per_sample() -> None:
    count = populate_qdrant_db([_sample(sample_id="S1"), _sample(sample_id="S2")])

    assert count == 2


def test_populate_qdrant_db_returns_zero_for_no_samples() -> None:
    assert populate_qdrant_db([]) == 0
    assert _FakeQdrantClient.last_init_kwargs is None
