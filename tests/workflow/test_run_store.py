"""The Work tab's runs, Agent 2 reviews and operator decisions are persisted to Qdrant (the source
project's three payload-only collections, via app/workflow/services/run_store.py) so the Review
Console survives a backend restart. tests/workflow/conftest.py points the store at an in-memory
Qdrant; the in-process registry in reviews.py is the cache that a "restart" clears here.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import WorkflowReviewDecision
from app.workflow.services import reviews, run_store
from app.workflow.services.run_store import REVIEWS, RUNS, SAMPLES

RUN_URL = "/api/orchestrator/reviews/run"
DECISION_URL = "/api/orchestrator/reviews/decision"
DECISIONS_URL = "/api/orchestrator/reviews/decisions"
CASES_URL = "/api/orchestrator/reviews/cases"


def _sample(sample_id: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "board": "Board1",
        "component": "U51",
        "defect_image": f"/abs/{sample_id}_defect.jpg",
        "golden_image": f"/abs/{sample_id}_golden.jpg",
        "failed_inspections": {"AI2": {"status": "Failed"}},
    }


def _result(sample_id: str, decision: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "final_decision": decision,
        "feature_classification": {"prediction": "Body", "confidence": 0.9},
        "defect_classification": {"prediction": "WrongPart", "confidence": 0.6},
    }


def _state(*decisions: str) -> dict[str, Any]:
    ids = [f"S{i + 1}" for i in range(len(decisions))]
    return {
        "status": "REVIEW_REQUIRED",
        "prepared_samples": [_sample(i) for i in ids],
        "verified_samples": [_sample(i) for i in ids],
        "inference_results": [_result(i, d) for i, d in zip(ids, decisions, strict=True)],
    }


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    reviews._cases.clear()
    reviews._reviews.clear()
    yield
    reviews._cases.clear()
    reviews._reviews.clear()


def _restart() -> None:
    """What a backend restart does to the in-process registry."""

    reviews._cases.clear()
    reviews._reviews.clear()


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch, output: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return output

    monkeypatch.setattr(reviews, "_execute_pipeline", lambda: execute)
    return calls


async def test_save_run_writes_the_three_collection_shapes(run_store_repo: Any) -> None:
    await run_store.save_run("run-a", _state("REVIEW_REQUIRED", "ACCEPTED"))

    run = run_store_repo.get(RUNS, "run-a")
    assert run["storage_status"] == "READY" and run["result"]["status"] == "REVIEW_REQUIRED"
    point = run_store_repo.get(SAMPLES, "run-a", "S1")
    assert point["final_decision"] == "REVIEW_REQUIRED"
    assert point["sample"]["component"] == "U51"
    # Agent 2 hasn't run yet, so no review point exists
    with pytest.raises(KeyError):
        run_store_repo.get(REVIEWS, "run-a", "S1")
    # only the REVIEW_REQUIRED sample is a review case
    assert [p["sample_id"] for p in await run_store.review_cases("run-a")] == ["S1"]


async def test_save_run_is_idempotent_but_rejects_different_content_for_the_same_id() -> None:
    state = _state("REVIEW_REQUIRED")
    await run_store.save_run("run-a", state)
    await run_store.save_run("run-a", state)

    with pytest.raises(run_store.Conflict):
        await run_store.save_run("run-a", _state("ACCEPTED"))


async def test_unreachable_qdrant_raises_store_unavailable_not_a_raw_error() -> None:
    class Broken:
        def save_run(self, run_id: str, result: dict[str, Any]) -> None:
            raise ConnectionError("qdrant down")

    run_store.set_repository(Broken())

    with pytest.raises(run_store.StoreUnavailable):
        await run_store.save_run("run-a", _state("REVIEW_REQUIRED"))


async def test_review_console_survives_a_restart(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await run_store.save_run("run-a", _state("REVIEW_REQUIRED", "ACCEPTED"))
    _restart()  # the registry is empty; only Qdrant knows the run

    cases = authenticated_client.get(CASES_URL, params={"run_id": "run-a"}).json()
    assert [c["sample_id"] for c in cases] == ["S1"]
    assert cases[0]["component_ref"] == "U51" and cases[0]["agent1_verdict"] == "wrong part"

    calls = _stub_pipeline(
        monkeypatch,
        {"predicted_defect": "missing part", "self_check_passed": True, "diagnosis": "bare pads"},
    )
    first = authenticated_client.post(RUN_URL, json={"run_id": "run-a", "sample_id": "S1"})
    assert first.status_code == 200, first.text
    assert first.json()["conflict"] is True
    assert calls[0]["component_ref"] == "U51"

    _restart()
    cases = authenticated_client.get(CASES_URL, params={"run_id": "run-a"}).json()
    assert cases[0]["review"]["agent2_verdict"] == "missing part"
    assert cases[0]["review"]["diagnosis"] == "bare pads"


async def test_first_agent2_review_is_immutable_so_a_second_ask_does_not_rerun_it(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch, run_store_repo: Any
) -> None:
    await run_store.save_run("run-a", _state("REVIEW_REQUIRED"))
    calls = _stub_pipeline(monkeypatch, {"predicted_defect": "missing part", "diagnosis": "d"})

    authenticated_client.post(RUN_URL, json={"run_id": "run-a", "sample_id": "S1"})
    _restart()  # even across a restart the stored review is returned, not regenerated
    again = authenticated_client.post(RUN_URL, json={"run_id": "run-a", "sample_id": "S1"})

    assert again.status_code == 200 and again.json()["agent2_verdict"] == "missing part"
    assert len(calls) == 1
    stored = run_store_repo.get(REVIEWS, "run-a", "S1")
    assert stored["result"]["review_status"] == "GENERATED_UNVALIDATED"
    assert stored["result"]["output"]["predicted_defect"] == "missing part"


async def test_decision_lands_on_the_review_point_and_survives_a_restart(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    run_store_repo: Any,
    db_async_session: AsyncSession,
) -> None:
    await run_store.save_run("run-a", _state("REVIEW_REQUIRED"))
    _stub_pipeline(monkeypatch, {"predicted_defect": "missing part", "diagnosis": "d"})
    authenticated_client.post(RUN_URL, json={"run_id": "run-a", "sample_id": "S1"})
    payload = {
        "run_id": "run-a",
        "sample_id": "S1",
        "selected_source": "AI",
        "final_result": "missing part",
        "machine_result": "wrong part",
        "ai_result": "missing part",
        "operator_notes": " bare pads ",
    }

    saved = authenticated_client.put(DECISION_URL, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["operator_notes"] == "bare pads"
    replaced = authenticated_client.put(
        DECISION_URL, json={**payload, "selected_source": "MANUAL", "final_result": "Tombstone"}
    )
    assert replaced.status_code == 200, replaced.text

    record = run_store_repo.get(REVIEWS, "run-a", "S1")
    # the decision sits next to Agent 2's untouched evidence
    assert record["result"]["output"]["predicted_defect"] == "missing part"
    assert record["review_status"] == "COMPLETED" and "reviewed_at_utc" in record
    assert record["human_decision"]["final_result"] == "Tombstone"
    assert record["human_decision"]["decided_by_user_id"]
    # ...and nothing was written to Postgres
    assert (await db_async_session.scalars(select(WorkflowReviewDecision))).all() == []

    _restart()
    listed = authenticated_client.get(DECISIONS_URL, params={"run_id": "run-a"}).json()
    assert [(d["sample_id"], d["selected_source"]) for d in listed] == [("S1", "MANUAL")]
    cases = authenticated_client.get(CASES_URL, params={"run_id": "run-a"}).json()
    assert cases[0]["decision"]["final_result"] == "Tombstone"


async def test_decision_for_a_run_qdrant_has_never_seen_falls_back_to_postgres(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    response = authenticated_client.put(
        DECISION_URL,
        json={
            "run_id": "old-run",
            "sample_id": "S1",
            "selected_source": "MACHINE",
            "final_result": "wrong part",
        },
    )

    assert response.status_code == 200, response.text
    (row,) = (await db_async_session.scalars(select(WorkflowReviewDecision))).all()
    assert (row.run_id, row.final_result) == ("old-run", "wrong part")
    listed = authenticated_client.get(DECISIONS_URL, params={"run_id": "old-run"}).json()
    assert [d["sample_id"] for d in listed] == ["S1"]
