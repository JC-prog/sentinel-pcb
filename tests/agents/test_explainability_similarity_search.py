from collections.abc import Callable
from typing import Any

import pytest
from PIL import Image

from app.agents.explainability_review_agent.mcp_client import PCBMCPClient


class _FakePoint:
    def __init__(self, score: float, payload: dict[str, Any]) -> None:
        self.score = score
        self.payload = payload


class _FakeQueryResponse:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class _FakeEncoder:
    def encode(self, image: Image.Image) -> Any:
        class _Vector:
            def tolist(self) -> list[float]:
                return [0.1, 0.2, 0.3]

        return _Vector()


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    query_points_impl: Callable[..., _FakeQueryResponse],
) -> PCBMCPClient:
    class _FakeQdrantClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def query_points(self, **kwargs: Any) -> _FakeQueryResponse:
            return query_points_impl(**kwargs)

    monkeypatch.setattr(
        "app.agents.explainability_review_agent.mcp_client.QdrantClient", _FakeQdrantClient
    )
    monkeypatch.setattr(
        "app.agents.explainability_review_agent.mcp_client.SentenceTransformer",
        lambda *args, **kwargs: _FakeEncoder(),
    )
    return PCBMCPClient(qdrant_path=str(tmp_path))


def test_search_historical_embeds_the_image_and_queries_qdrant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    captured: dict[str, Any] = {}

    def query_points_impl(**kwargs: Any) -> _FakeQueryResponse:
        captured.update(kwargs)
        return _FakeQueryResponse(
            [_FakePoint(0.93, {"defect_type": "Tombstone", "status": "Failed"})]
        )

    client = _build_client(monkeypatch, tmp_path, query_points_impl)
    results = client.search_historical(Image.new("RGB", (4, 4)), component_ref="U7")

    assert captured["collection_name"] == "pcb_defects"
    assert captured["query"] == [0.1, 0.2, 0.3]
    assert results == [{"score": 0.93, "defect_category": "Tombstone", "root_cause": "Failed"}]


def test_search_historical_omits_filter_when_no_component_ref_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    captured: dict[str, Any] = {}

    def query_points_impl(**kwargs: Any) -> _FakeQueryResponse:
        captured.update(kwargs)
        return _FakeQueryResponse([])

    client = _build_client(monkeypatch, tmp_path, query_points_impl)
    client.search_historical(Image.new("RGB", (4, 4)))

    assert captured["query_filter"] is None


def test_search_historical_degrades_to_empty_list_on_any_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    def query_points_impl(**kwargs: Any) -> _FakeQueryResponse:
        raise RuntimeError("qdrant unavailable")

    client = _build_client(monkeypatch, tmp_path, query_points_impl)
    assert client.search_historical(Image.new("RGB", (4, 4)), component_ref="U7") == []
