"""The sample agent: read-only lookups of what the Work tab stored in Qdrant about a dataset sample.

The stored points are produced by the workflow's own writer (run_store's Repository, over a local
in-memory Qdrant) and copied into the async in-memory client the chat reader uses - so these tests
read the real payload shapes, and a change to what the workflow writes breaks them here.
"""

import json
import warnings
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from qdrant_client import AsyncQdrantClient, QdrantClient, models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents import tool_registry
from app.chat.agents.monitoring_agent import DRAFT_RETRAINING_PLAN
from app.chat.agents.sample_agent import GET_RUN_DRIFT, GET_SAMPLE, LIST_REVIEW_CASES
from app.chat.services import run_samples
from app.shared.config.settings import settings
from app.shared.db.models import RetrainingJob, UserRole
from app.shared.modelops import tickets as ticket_repo
from app.shared.modelops.versions import sync_versions
from app.workflow.services import monitoring, run_store
from app.workflow.services.schemas import WorkflowQueueCorrectionsRequest
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_user, model_info

COLLECTIONS = (run_store.RUNS, run_store.SAMPLES, run_store.REVIEWS)


def _sample(sample_id: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "board": "Board1",
        "package": "35-900032-AAA-RV1",
        "component": "U51",
        "source_feature": "Text1",
        "machine_defect": "WrongPart_13",
        "timestamp": "20260908_144902427",
        "golden_image": f"C:/secret/{sample_id}_golden.png",
        "defect_image": f"C:/secret/{sample_id}_defect.png",
        "failed_inspections": {
            "AI2": {
                "status": "Failed",
                "failed_criteria": ["ConfidenceLevel"],
                "measurements": {
                    "ConfidenceLevel": {"Value": "42.8", "Minimum": "1", "Maximum": "100", "Target": "50"},
                    "Polarity": {"IsFailed": "false"},
                },
            }
        },
        "xml_feature_status": "Failed",
        "preparation_status": "READY",
    }


def _completed(sample_id: str, decision: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "status": "COMPLETED",
        "final_decision": decision,
        "errors": [],
        "feature_classification": {"prediction": "Body", "confidence": 0.93},
        "defect_classification": {"prediction": "WrongPart", "confidence": 0.61234},
    }


def _stage_one_only(sample_id: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "status": "FEATURE_CLASSIFICATION_UNCERTAIN",
        "final_decision": "REVIEW_REQUIRED",
        "errors": [],
        "details": {"feature_classification": {"prediction": "Text", "confidence": 0.31}},
    }


def _state(inference: list[dict[str, Any]], status: str = "REVIEW_REQUIRED") -> dict[str, Any]:
    ids = [i["sample_id"] for i in inference]
    return {
        "status": status,
        "prepared_samples": [_sample(i) for i in ids],
        "verified_samples": [_sample(i) for i in ids],
        "inference_results": inference,
    }


def _repo() -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "payload indexes have no effect in the local Qdrant"
        return run_store.build_repository(QdrantClient(":memory:"))


async def _publish(repo: Any, client: AsyncQdrantClient) -> None:
    """Copies every point the workflow's Repository wrote into the client the chat reader uses."""

    for name in COLLECTIONS:
        await client.create_collection(name, vectors_config={})
        points, _ = repo.client.scroll(name, limit=1000, with_payload=True, with_vectors=False)
        if points:
            await client.upsert(
                name,
                [models.PointStruct(id=p.id, vector={}, payload=p.payload) for p in points],
            )


@pytest.fixture
async def client() -> AsyncIterator[AsyncQdrantClient]:
    qdrant = AsyncQdrantClient(":memory:")
    run_samples.set_client(qdrant)
    yield qdrant
    run_samples.set_client(None)
    await qdrant.close()


def _ctx() -> Any:
    return tool_context(cast(Any, None), cast(Any, None))


def _review(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_status": "GENERATED_UNVALIDATED",
        "source": "agent2_web_pipeline",
        "output": output,
        "warning": "Validate evidence before promoting to approved verdict.",
    }


def test_the_reader_names_the_same_collections_the_workflow_writes() -> None:
    assert (run_samples.RUNS, run_samples.SAMPLES, run_samples.REVIEWS) == COLLECTIONS


async def test_get_sample_reports_what_the_run_stored(client: AsyncQdrantClient) -> None:
    repo = _repo()
    repo.save_run("run-a", _state([_completed("S1", "REVIEW_REQUIRED")]))
    await _publish(repo, client)

    result = await call(GET_SAMPLE, _ctx(), sample_id="S1")

    assert result["sample_id"] == "S1" and result["run_id"] == "run-a"
    assert (result["board"], result["component"], result["machine_defect"]) == (
        "Board1",
        "U51",
        "WrongPart_13",
    )
    assert result["agent1"]["final_decision"] == "REVIEW_REQUIRED"
    assert result["agent1"]["feature"] == {"label": "Body", "confidence": 0.93}
    assert result["agent1"]["defect"] == {"label": "WrongPart", "confidence": 0.6123}
    # only the failed criterion's measurement is reported
    (inspection,) = result["failed_inspections"]
    assert inspection["failed_criteria"] == ["ConfidenceLevel"]
    assert list(inspection["measurements"]) == ["ConfidenceLevel"]
    assert result["agent2"] is None and result["human_decision"] is None
    assert result["review_state"] == "awaiting_agent2"
    assert "other_runs" not in result
    # server-side image paths are not handed to the model
    assert "secret" not in json.dumps(result)


async def test_get_sample_includes_agent2_and_the_operators_decision(
    client: AsyncQdrantClient,
) -> None:
    repo = _repo()
    repo.save_run("run-a", _state([_completed("S1", "REVIEW_REQUIRED")]))
    repo.save_review(
        "run-a",
        "S1",
        _review(
            {
                "predicted_defect": "missing part",
                "final_confidence": 0.95,
                "diagnosis": "Laser height 0",
                "contradiction_detected": True,
                "self_check_passed": False,
                "ipc_citations": ["IPC-A-610 Section 8.3.1"],
                "errors": [],
            }
        ),
    )
    await _publish(repo, client)

    awaiting = await call(GET_SAMPLE, _ctx(), sample_id="S1")
    assert awaiting["review_state"] == "awaiting_operator"
    assert awaiting["agent2"]["predicted_defect"] == "missing part"
    assert awaiting["agent2"]["diagnosis"] == "Laser height 0"
    assert awaiting["agent2"]["ipc_citations"] == ["IPC-A-610 Section 8.3.1"]
    assert awaiting["agent2"]["review_status"] == "GENERATED_UNVALIDATED"

    repo.save_decision(
        "run-a",
        "S1",
        {"selected_source": "AI", "final_result": "missing part", "operator_notes": "bare pads"},
    )
    await client.delete_collection(run_samples.REVIEWS)
    await client.create_collection(run_samples.REVIEWS, vectors_config={})
    points, _ = repo.client.scroll(run_store.REVIEWS, limit=10, with_payload=True)
    await client.upsert(
        run_samples.REVIEWS,
        [models.PointStruct(id=p.id, vector={}, payload=p.payload) for p in points],
    )

    decided = await call(GET_SAMPLE, _ctx(), sample_id="S1")
    assert decided["review_state"] == "decided"
    assert decided["human_decision"]["final_result"] == "missing part"
    assert decided["human_decision"]["operator_notes"] == "bare pads"
    assert decided["human_decision"]["decided_at"]
    assert decided["agent2"]["predicted_defect"] == "missing part"  # evidence kept beside it


async def test_a_sample_in_several_runs_answers_from_the_latest_and_names_the_others(
    client: AsyncQdrantClient,
) -> None:
    repo = _repo()
    repo.save_run("run-old", _state([_completed("S1", "ACCEPTED")], status="ACCEPTED"))
    repo.save_run("run-new", _state([_completed("S1", "REVIEW_REQUIRED")]))
    await _publish(repo, client)

    latest = await call(GET_SAMPLE, _ctx(), sample_id="S1")
    assert latest["run_id"] == "run-new"
    assert [r["run_id"] for r in latest["other_runs"]] == ["run-old"]

    older = await call(GET_SAMPLE, _ctx(), sample_id="S1", run_id="run-old")
    assert older["run_id"] == "run-old"
    assert older["agent1"]["final_decision"] == "ACCEPTED"
    assert older["review_state"] == "not_flagged"
    assert "other_runs" not in older


async def test_a_run_that_is_still_being_written_is_not_read(client: AsyncQdrantClient) -> None:
    repo = _repo()
    repo.save_run("run-a", _state([_completed("S1", "REVIEW_REQUIRED")]))
    run = repo.get(run_store.RUNS, "run-a")
    repo.put(run_store.RUNS, ("run-a",), {**run, "storage_status": "WRITING"})
    await _publish(repo, client)

    result = await call(GET_SAMPLE, _ctx(), sample_id="S1")

    assert "error" in result and "run-a" not in result.get("run_id", "")


async def test_a_sample_that_stopped_at_feature_classification_has_no_defect_prediction(
    client: AsyncQdrantClient,
) -> None:
    repo = _repo()
    repo.save_run("run-a", _state([_stage_one_only("S1")]))
    await _publish(repo, client)

    result = await call(GET_SAMPLE, _ctx(), sample_id="S1")

    assert result["agent1"]["feature"] == {"label": "Text", "confidence": 0.31}
    assert result["agent1"]["defect"] is None
    assert result["agent1"]["status"] == "FEATURE_CLASSIFICATION_UNCERTAIN"


async def test_unknown_sample_is_refused_with_the_latest_run_as_a_hint(
    client: AsyncQdrantClient,
) -> None:
    repo = _repo()
    repo.save_run("run-a", _state([_completed("S1", "ACCEPTED")]))
    await _publish(repo, client)

    result = await call(GET_SAMPLE, _ctx(), sample_id="S999")

    assert "S999" in result["error"] and "run-a" in result["error"]
    assert "error" in await call(GET_SAMPLE, _ctx(), sample_id="S1", run_id="no-such-run")
    assert "required" in (await call(GET_SAMPLE, _ctx(), sample_id="  "))["error"]


async def test_with_nothing_stored_yet_the_error_says_to_run_a_dataset(
    client: AsyncQdrantClient,
) -> None:
    result = await call(GET_SAMPLE, _ctx(), sample_id="S1")

    assert "run a dataset in the Work tab" in result["error"]
    assert "run a dataset" in (await call(LIST_REVIEW_CASES, _ctx()))["error"]


async def test_an_unreachable_store_is_a_refusal_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Down:
        async def collection_exists(self, name: str) -> bool:
            raise ConnectionError("qdrant down")

    run_samples.set_client(cast(Any, Down()))
    try:
        result = await call(GET_SAMPLE, _ctx(), sample_id="S1")
        listed = await call(LIST_REVIEW_CASES, _ctx())
    finally:
        run_samples.set_client(None)

    assert "could not be reached" in result["error"]
    assert "could not be reached" in listed["error"]


async def test_list_review_cases_defaults_to_the_latest_run_and_reports_each_state(
    client: AsyncQdrantClient,
) -> None:
    repo = _repo()
    repo.save_run("run-old", _state([_completed("S9", "REVIEW_REQUIRED")]))
    repo.save_run(
        "run-new",
        _state(
            [
                _completed("S1", "REVIEW_REQUIRED"),
                _completed("S2", "ACCEPTED"),
                _stage_one_only("S3"),
                _completed("S4", "REVIEW_REQUIRED"),
            ]
        ),
    )
    repo.save_review("run-new", "S1", _review({"predicted_defect": "missing part"}))
    repo.save_decision("run-new", "S4", {"selected_source": "MACHINE", "final_result": "wrong part"})
    await _publish(repo, client)

    result = await call(LIST_REVIEW_CASES, _ctx())

    assert (result["run_id"], result["total"]) == ("run-new", 3)
    by_id = {c["sample_id"]: c for c in result["cases"]}
    assert set(by_id) == {"S1", "S3", "S4"}  # the accepted sample is not a review case
    assert (by_id["S1"]["agent1"], by_id["S1"]["agent2"]) == ("WrongPart", "missing part")
    assert by_id["S1"]["review_state"] == "awaiting_operator"
    assert by_id["S3"]["agent1"] == "Text" and by_id["S3"]["review_state"] == "awaiting_agent2"
    assert by_id["S4"]["review_state"] == "decided" and by_id["S4"]["final_result"] == "wrong part"

    older = await call(LIST_REVIEW_CASES, _ctx(), run_id="run-old")
    assert [c["sample_id"] for c in older["cases"]] == ["S9"]
    capped = await call(LIST_REVIEW_CASES, _ctx(), limit=2)
    assert (capped["total"], capped["shown"], len(capped["cases"])) == (3, 2, 2)
    assert "error" in await call(LIST_REVIEW_CASES, _ctx(), run_id="nope")


def test_the_tools_are_registered_for_qa_and_admin_and_follow_the_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def names(role: UserRole) -> set[str]:
        return {t.name for t in tool_registry.available(role=role, has_image=False)}

    for role in (UserRole.QA, UserRole.ADMIN):
        assert {"get_sample", "list_review_cases", "get_run_drift"} <= names(role)

    monkeypatch.setattr(settings, "sample_lookup_agent_enabled", False)
    assert not {"get_sample", "list_review_cases", "get_run_drift"} & names(UserRole.QA)


# --- get_run_drift: how the models did in a run, and what the operator corrected ----------------


def _routed(sample_id: str, label: str = "MissingPart") -> dict[str, Any]:
    """A completed sample that reached a defect model (so it can be ticketed)."""

    return {
        **_completed(sample_id, "REVIEW_REQUIRED"),
        "routing": {"selected_model": "body", "service_model": "pcb_body_defect"},
        "defect_classification": {
            "prediction": label,
            "confidence": 0.61,
            "model_version": "JcProg/body@v2",
        },
    }


async def _run_with_one_correction(client: AsyncQdrantClient) -> Any:
    repo = _repo()
    repo.save_run("run-a", _state([_routed("S1"), _routed("S2")]))
    repo.save_decision(
        "run-a", "S1", {"selected_source": "MACHINE", "final_result": "missing part"}
    )
    repo.save_decision(
        "run-a",
        "S2",
        {"selected_source": "MANUAL", "final_result": "Tombstone", "operator_notes": "bare pads"},
    )
    await _publish(repo, client)
    return repo


async def test_get_run_drift_reports_the_operators_corrections_and_who_still_needs_queueing(
    client: AsyncQdrantClient, db_async_session: AsyncSession
) -> None:
    await _run_with_one_correction(client)
    user = await make_user(db_async_session)
    ctx = tool_context(db_async_session, user)

    result = await call(GET_RUN_DRIFT, ctx)

    assert result["run_id"] == "run-a"
    assert result["totals"] == {"samples": 2, "review_required": 2, "decided": 2, "corrected": 1}
    (model,) = result["models"]
    assert (model["model_name"], model["corrected"], model["correction_rate"]) == (
        "pcb_body_defect",
        1,
        0.5,
    )
    (correction,) = result["corrections"]
    assert (correction["sample_id"], correction["agent1_label"], correction["final_result"]) == (
        "S2",
        "MissingPart",
        "Tombstone",
    )
    assert correction["queued"] is False and result["corrections_not_queued"] == 1
    assert "Drift & Retraining tab" in result["instruction"]
    assert "draft_retraining_plan" not in result["instruction"]  # nothing queued yet

    await ticket_repo.create_ticket(
        db_async_session,
        flagged_by_user_id=user.id,
        reason="operator correction",
        sample_ref="S2",
        run_id="run-a",
        model_name="pcb_body_defect",
    )
    queued = await call(GET_RUN_DRIFT, ctx)
    assert queued["corrections"][0]["queued"] is True and queued["corrections_not_queued"] == 0
    assert "draft_retraining_plan" in queued["instruction"] and "pcb_body_defect" in queued["instruction"]


async def test_get_run_drift_defaults_to_the_latest_run_and_refuses_unknown_ones(
    client: AsyncQdrantClient, db_async_session: AsyncSession
) -> None:
    assert "run a dataset" in (await call(GET_RUN_DRIFT, _ctx()))["error"]

    repo = _repo()
    repo.save_run("run-old", _state([_routed("S9")]))
    repo.save_run("run-new", _state([_routed("S1")]))
    await _publish(repo, client)
    user = await make_user(db_async_session)
    ctx = tool_context(db_async_session, user)

    assert (await call(GET_RUN_DRIFT, ctx))["run_id"] == "run-new"
    assert (await call(GET_RUN_DRIFT, ctx, run_id="run-old"))["run_id"] == "run-old"
    assert "error" in await call(GET_RUN_DRIFT, ctx, run_id="nope")


async def test_a_ticket_queued_from_the_work_tab_is_drafted_into_a_plan_by_the_chat_agent(
    db_async_session: AsyncSession,
) -> None:
    """The chain end to end: the operator's correction is queued by the workflow, and chat's
    monitoring agent turns the open ticket into a retraining job awaiting Admin approval."""

    repo = _repo()
    repo.save_run("run-a", _state([_routed("S1"), _routed("S2")]))
    repo.save_decision("run-a", "S2", {"selected_source": "MANUAL", "final_result": "Tombstone"})
    user = await make_user(db_async_session)
    run_store.set_repository(repo)
    try:
        queued = await monitoring.queue_corrections(
            db_async_session,
            request=WorkflowQueueCorrectionsRequest(run_id="run-a", sample_ids=["S2"]),
            user=user,
        )
    finally:
        run_store.set_repository(None)
    assert len(queued.created) == 1
    await sync_versions(db_async_session, [model_info("pcb_body_defect", "JcProg/body@v2")])

    plan = await call(
        DRAFT_RETRAINING_PLAN, tool_context(db_async_session, user), model="pcb_body_defect"
    )

    assert plan["status"] == "pending_approval", plan
    (job,) = (await db_async_session.scalars(select(RetrainingJob))).all()
    (sample,) = job.samples
    assert (sample["sample_ref"], sample["run_id"]) == ("S2", "run-a")
    assert (sample["observed_label"], sample["expected_label"]) == ("MissingPart", "Tombstone")
