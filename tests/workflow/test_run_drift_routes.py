"""The Drift & Retraining tab's server side: GET /api/orchestrator/monitoring/run-drift (per-model drift
and the operator's corrections, computed from the run stored in Qdrant) and POST
.../run-retraining-tickets (queue a ticket for each correction, built from the stored sample and
decision). The decisions are saved through the real review route, so these tests cover the chain
"operator relabels in Explanation Review -> the tab's numbers follow -> queue for retraining"."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db.models import DriftReport, RetrainingJob, RetrainingTicket
from app.workflow.services import run_store
from tests.modelops.fake_inference import FakeInference, install

DRIFT_URL = "/api/orchestrator/monitoring/run-drift"
QUEUE_URL = "/api/orchestrator/monitoring/run-retraining-tickets"
DECISION_URL = "/api/orchestrator/reviews/decision"
REPORT_URL = "/api/orchestrator/monitoring/drift-report"
FLAG_URL = "/api/orchestrator/monitoring/retraining-tickets"
PLAN_URL = "/api/orchestrator/monitoring/retraining-plan"

RUN = "run-a"


def _sample(sample_id: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "board": "Board1",
        "component": "U51",
        "machine_defect": "WrongPart_13",
        "defect_image": f"/abs/{sample_id}.png",
        "golden_image": f"/abs/{sample_id}_g.png",
        "failed_inspections": {},
    }


def _routed(sample_id: str, label: str = "MissingPart", confidence: float = 0.6) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "final_decision": "REVIEW_REQUIRED",
        "feature_classification": {"prediction": "Body", "confidence": 0.95, "model_version": "JcProg/region@v1"},
        "routing": {"selected_model": "body", "service_model": "pcb_body_defect"},
        "defect_classification": {
            "prediction": label,
            "confidence": confidence,
            "model_version": "JcProg/body@v2",
        },
    }


def _stage_one_only(sample_id: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "final_decision": "REVIEW_REQUIRED",
        "status": "FEATURE_CLASSIFICATION_UNCERTAIN",
        "details": {"feature_classification": {"prediction": "Text", "confidence": 0.3}},
    }


def _store_run(repo: Any, *inference: dict[str, Any], run_id: str = RUN) -> None:
    ids = [i["sample_id"] for i in inference]
    repo.save_run(
        run_id,
        {
            "status": "REVIEW_REQUIRED",
            "prepared_samples": [_sample(i) for i in ids],
            "verified_samples": [_sample(i) for i in ids],
            "inference_results": list(inference),
        },
    )


def _decide(
    client: TestClient, sample_id: str, final: str, source: str = "MANUAL", notes: str | None = None
) -> None:
    response = client.put(
        DECISION_URL,
        json={
            "run_id": RUN,
            "sample_id": sample_id,
            "selected_source": source,
            "final_result": final,
            "operator_notes": notes,
        },
    )
    assert response.status_code == 200, response.text


def _drift(client: TestClient, run_id: str = RUN) -> dict[str, Any]:
    response = client.get(DRIFT_URL, params={"run_id": run_id})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _tickets(session: AsyncSession) -> list[RetrainingTicket]:
    return list((await session.scalars(select(RetrainingTicket))).all())


def test_the_routes_require_login(client: TestClient) -> None:
    assert client.get(DRIFT_URL, params={"run_id": RUN}).status_code == 401
    assert client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S1"]}).status_code == 401


@pytest.mark.parametrize("setting_name", ["orchestrator_agent_enabled", "modelops_enabled"])
def test_the_routes_need_both_kill_switches(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch, setting_name: str
) -> None:
    monkeypatch.setattr(settings, setting_name, False)

    assert authenticated_client.get(DRIFT_URL, params={"run_id": RUN}).status_code == 503
    queued = authenticated_client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S1"]})
    assert queued.status_code == 503


def test_an_unknown_run_is_a_404(authenticated_client: TestClient) -> None:
    assert authenticated_client.get(DRIFT_URL, params={"run_id": "nope"}).status_code == 404
    queued = authenticated_client.post(QUEUE_URL, json={"run_id": "nope", "sample_ids": ["S1"]})
    assert queued.status_code == 404


def test_the_tab_follows_each_decision_the_operator_saves(
    authenticated_client: TestClient, run_store_repo: Any
) -> None:
    _store_run(run_store_repo, _routed("S1"), _routed("S2"), _routed("S3"))

    before = _drift(authenticated_client)
    assert before["available"] is True
    assert before["totals"] == {"samples": 3, "review_required": 3, "decided": 0, "corrected": 0}
    assert before["corrections"] == []

    _decide(authenticated_client, "S1", "missing part", source="MACHINE")  # agrees with Agent 1
    _decide(authenticated_client, "S2", "Tombstone", notes="bare pads")  # corrects it

    after = _drift(authenticated_client)
    assert after["totals"] == {"samples": 3, "review_required": 3, "decided": 2, "corrected": 1}
    (model,) = after["models"]
    assert (model["model_name"], model["model_version"]) == ("pcb_body_defect", "JcProg/body@v2")
    assert (model["decided"], model["corrected"], model["correction_rate"]) == (2, 1, 0.5)
    (correction,) = after["corrections"]
    assert correction == {
        "sample_id": "S2",
        "model_name": "pcb_body_defect",
        "model_version": "JcProg/body@v2",
        "agent1_label": "MissingPart",
        "final_result": "Tombstone",
        "selected_source": "MANUAL",
        "operator_notes": "bare pads",
        "queueable": True,
        "queued": False,
    }

    # relabelling again replaces the decision, and the tab follows that too
    _decide(authenticated_client, "S2", "missing part", source="MACHINE")
    assert _drift(authenticated_client)["corrections"] == []


async def test_queueing_a_correction_files_a_ticket_built_from_the_stored_decision(
    authenticated_client: TestClient, run_store_repo: Any, db_async_session: AsyncSession
) -> None:
    _store_run(run_store_repo, _routed("S1"), _routed("S2"))
    _decide(authenticated_client, "S2", "Tombstone", source="AI", notes="bare pads")

    response = authenticated_client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S2"]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [t["sample_ref"] for t in body["created"]] == ["S2"] and body["already_queued"] == []
    (ticket,) = await _tickets(db_async_session)
    assert (ticket.run_id, ticket.sample_ref, ticket.case_id) == (RUN, "S2", None)
    assert (ticket.model_name, ticket.model_version) == ("pcb_body_defect", "JcProg/body@v2")
    assert (ticket.observed_label, ticket.correct_label) == ("MissingPart", "Tombstone")
    assert "MissingPart -> Tombstone" in ticket.reason and "bare pads" in ticket.reason
    assert _drift(authenticated_client)["corrections"][0]["queued"] is True


async def test_queueing_twice_files_nothing_twice(
    authenticated_client: TestClient, run_store_repo: Any, db_async_session: AsyncSession
) -> None:
    _store_run(run_store_repo, _routed("S1"))
    _decide(authenticated_client, "S1", "Tombstone")
    payload = {"run_id": RUN, "sample_ids": ["S1"]}

    first = authenticated_client.post(QUEUE_URL, json=payload).json()
    second = authenticated_client.post(QUEUE_URL, json=payload).json()

    assert len(first["created"]) == 1
    assert second == {"created": [], "already_queued": ["S1"]}
    assert len(await _tickets(db_async_session)) == 1


async def test_a_selection_with_a_sample_that_was_not_corrected_is_rejected_whole(
    authenticated_client: TestClient, run_store_repo: Any, db_async_session: AsyncSession
) -> None:
    _store_run(run_store_repo, _routed("S1"), _routed("S2"), _routed("S3"))
    _decide(authenticated_client, "S1", "Tombstone")  # corrected
    _decide(authenticated_client, "S2", "missing part", source="MACHINE")  # agreed

    response = authenticated_client.post(
        QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S1", "S2", "S3", "S404"]}
    )

    assert response.status_code == 422
    for sample_id in ("S2", "S3", "S404"):
        assert sample_id in response.json()["detail"]
    assert await _tickets(db_async_session) == []  # S1 was valid, but nothing is half-filed


async def test_a_correction_with_no_model_recorded_cannot_be_queued(
    authenticated_client: TestClient, run_store_repo: Any, db_async_session: AsyncSession
) -> None:
    _store_run(run_store_repo, _routed("S1"), _stage_one_only("S2"))
    _decide(authenticated_client, "S1", "Tombstone")
    _decide(authenticated_client, "S2", "Tombstone")

    corrections = {c["sample_id"]: c for c in _drift(authenticated_client)["corrections"]}
    assert corrections["S2"]["queueable"] is False and corrections["S2"]["model_name"] is None

    response = authenticated_client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S1", "S2"]})

    assert response.status_code == 422 and "S2" in response.json()["detail"]
    assert await _tickets(db_async_session) == []


def test_an_unreachable_store_gives_an_empty_summary_and_a_503_to_queue(
    authenticated_client: TestClient,
) -> None:
    class Down:
        def ready_run(self, run_id: str) -> None:
            raise ConnectionError("qdrant down")

    run_store.set_repository(Down())

    summary = _drift(authenticated_client)
    assert summary["available"] is False and "could not be read" in summary["message"]
    assert summary["models"] == [] and summary["corrections"] == []
    queued = authenticated_client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S1"]})
    assert queued.status_code == 503


async def test_a_drift_report_for_a_run_snapshots_the_servers_numbers(
    authenticated_client: TestClient, run_store_repo: Any, db_async_session: AsyncSession
) -> None:
    _store_run(run_store_repo, _routed("S1"), _routed("S2"))
    _decide(authenticated_client, "S2", "Tombstone")

    response = authenticated_client.post(
        REPORT_URL,
        json={"model_name": "pcb_body_defect", "description": "labels look off", "run_id": RUN},
    )

    assert response.status_code == 200, response.text
    (report,) = (await db_async_session.scalars(select(DriftReport))).all()
    assert report.stats["run_id"] == RUN
    assert report.stats["run_totals"]["corrected"] == 1
    assert report.stats["model"]["model_name"] == "pcb_body_defect"
    assert report.stats["model"]["correction_rate"] == 1.0


async def test_a_drift_report_for_an_unstored_run_is_still_filed(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    response = authenticated_client.post(
        REPORT_URL, json={"model_name": "pcb_body_defect", "description": "x", "run_id": "gone"}
    )

    assert response.status_code == 200, response.text
    (report,) = (await db_async_session.scalars(select(DriftReport))).all()
    assert report.stats["run_id"] == "gone" and "model" not in report.stats


async def test_the_manual_flag_form_does_not_double_flag_a_queued_sample(
    authenticated_client: TestClient, run_store_repo: Any, db_async_session: AsyncSession
) -> None:
    _store_run(run_store_repo, _routed("S1"), _routed("S2"))
    _decide(authenticated_client, "S1", "Tombstone")
    authenticated_client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S1"]})

    response = authenticated_client.post(
        FLAG_URL,
        json={
            "run_id": RUN,
            "tickets": [
                {"sample": _routed("S1"), "reason": "looks wrong"},
                {"sample": _routed("S2"), "reason": "looks wrong"},
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert [t["sample_ref"] for t in response.json()] == ["S2"]
    assert sorted(t.sample_ref or "" for t in await _tickets(db_async_session)) == ["S1", "S2"]


# --- drafting the plan from the Work tab, and approving it --------------------------------------


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeInference:
    fake = FakeInference()
    install(monkeypatch, fake)
    return fake


async def test_the_drift_numbers_say_how_many_tickets_are_waiting_for_a_plan(
    authenticated_client: TestClient, run_store_repo: Any
) -> None:
    _store_run(run_store_repo, _routed("S1"), _routed("S2"))
    assert _drift(authenticated_client)["open_tickets"] == {}

    _decide(authenticated_client, "S1", "Tombstone")
    _decide(authenticated_client, "S2", "Shift")
    authenticated_client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S1", "S2"]})

    assert _drift(authenticated_client)["open_tickets"] == {"pcb_body_defect": 2}


async def test_a_plan_drafted_from_the_work_tab_is_a_job_awaiting_approval(
    authenticated_client: TestClient, run_store_repo: Any, db_async_session: AsyncSession
) -> None:
    _store_run(run_store_repo, _routed("S1"), _routed("S2"))
    _decide(authenticated_client, "S1", "Tombstone")
    _decide(authenticated_client, "S2", "Shift")
    authenticated_client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S1", "S2"]})

    response = authenticated_client.post(PLAN_URL, json={"model_name": "pcb_body_defect"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["model_name"], body["status"], body["sample_count"]) == (
        "pcb_body_defect",
        "pending_approval",
        2,
    )
    assert body["base_version"] == "JcProg/body@v2"  # from the tickets - no live version needed
    (job,) = (await db_async_session.scalars(select(RetrainingJob))).all()
    assert job.id == body["job_id"]
    assert {(s["sample_ref"], s["run_id"]) for s in job.samples} == {("S1", RUN), ("S2", RUN)}
    # the tickets are now part of a plan, so none are waiting any more
    assert _drift(authenticated_client)["open_tickets"] == {}
    again = authenticated_client.post(PLAN_URL, json={"model_name": "pcb_body_defect"})
    assert again.status_code == 409 and "no open retraining tickets" in again.json()["detail"]


def test_a_plan_needs_tickets_and_both_kill_switches(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    nothing = authenticated_client.post(PLAN_URL, json={"model_name": "pcb_body_defect"})
    assert nothing.status_code == 409 and "queue some corrections first" in nothing.json()["detail"]
    assert authenticated_client.post(PLAN_URL, json={"model_name": ""}).status_code == 422

    monkeypatch.setattr(settings, "modelops_enabled", False)
    assert authenticated_client.post(PLAN_URL, json={"model_name": "pcb_body_defect"}).status_code == 503


def test_the_plan_route_requires_login(client: TestClient) -> None:
    assert client.post(PLAN_URL, json={"model_name": "pcb_body_defect"}).status_code == 401


async def test_the_whole_chain_from_a_decision_to_an_approved_job(
    authenticated_client: TestClient, run_store_repo: Any, fake: FakeInference
) -> None:
    """Decision -> queued correction -> plan from the Work tab -> an Admin approves it in the Models
    tab -> the inference service is sent the Work-tab samples (named by run and sample)."""

    _store_run(run_store_repo, _routed("S000001"))
    _decide(authenticated_client, "S000001", "Tombstone", notes="pad lifted")
    authenticated_client.post(QUEUE_URL, json={"run_id": RUN, "sample_ids": ["S000001"]})
    plan = authenticated_client.post(PLAN_URL, json={"model_name": "pcb_body_defect"}).json()

    queue = authenticated_client.get("/api/models/retraining/queue").json()
    assert [j["id"] for j in queue["jobs"]] == [plan["job_id"]]  # now visible in the Models tab

    approved = authenticated_client.post(f"/api/models/retraining/jobs/{plan['job_id']}/approve")

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "queued" and approved.json()["error"] is None
    (remote,) = fake.jobs.values()
    assert [s["case_id"] for s in remote["samples"]] == [f"{RUN}:S000001"]
    assert [(s["observed_label"], s["expected_label"]) for s in remote["samples"]] == [
        ("MissingPart", "Tombstone")
    ]
