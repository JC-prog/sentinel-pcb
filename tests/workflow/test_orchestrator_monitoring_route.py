"""Covers POST /api/orchestrator/monitoring/{drift-report,retraining-tickets} - the Work tab's
manual "report drift"/"flag for retraining" actions on a finished bulk run's results, filed into
the same app/shared/modelops/ tables the chat monitoring agent and Models tab use. Mirrors
test_orchestrator_run_route.py's gate-checking style (401/503) - a QA/Admin-only 403 case isn't
constructible in this app (UserRole is only QA|ADMIN; the existing route tests don't cover it
either), so it's not tested here.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db.models import DriftReport, RetrainingTicket

DRIFT_URL = "/api/orchestrator/monitoring/drift-report"
TICKETS_URL = "/api/orchestrator/monitoring/retraining-tickets"


def _completed_sample(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sample_id": "S1",
        "final_decision": "REVIEW_REQUIRED",
        "feature_classification": {
            "prediction": "Body",
            "confidence": 0.95,
            "model_version": "JcProg/region@v1",
        },
        "routing": {"selected_model": "body", "service_model": "pcb_body_defect"},
        "defect_classification": {
            "prediction": "MissingPart",
            "confidence": 0.6,
            "model_version": "JcProg/body@v2",
        },
    }
    base.update(overrides)
    return base


def _stage_one_only_sample(**overrides: Any) -> dict[str, Any]:
    """Never reached routing/stage 2 - no service_model, so no resolvable model name."""

    base: dict[str, Any] = {
        "sample_id": "S2",
        "status": "FEATURE_CLASSIFICATION_UNCERTAIN",
        "final_decision": "REVIEW_REQUIRED",
        "details": {
            "feature_classification": {
                "prediction": "Body",
                "confidence": 0.2,
                "model_version": "JcProg/region@v1",
            }
        },
    }
    base.update(overrides)
    return base


def test_drift_report_requires_login(client: TestClient) -> None:
    response = client.post(DRIFT_URL, json={"model_name": "pcb_body_defect", "description": "x"})
    assert response.status_code == 401


def test_tickets_route_requires_login(client: TestClient) -> None:
    response = client.post(TICKETS_URL, json={"tickets": [{"sample": {}, "reason": "x"}]})
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("setting_name", "url", "body"),
    [
        ("orchestrator_agent_enabled", DRIFT_URL, {"model_name": "pcb_body_defect", "description": "x"}),
        ("modelops_enabled", DRIFT_URL, {"model_name": "pcb_body_defect", "description": "x"}),
        (
            "orchestrator_agent_enabled",
            TICKETS_URL,
            {"tickets": [{"sample": _completed_sample(), "reason": "x"}]},
        ),
        (
            "modelops_enabled",
            TICKETS_URL,
            {"tickets": [{"sample": _completed_sample(), "reason": "x"}]},
        ),
    ],
)
def test_each_route_requires_both_kill_switches(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    setting_name: str,
    url: str,
    body: dict[str, Any],
) -> None:
    monkeypatch.setattr(settings, setting_name, False)
    assert authenticated_client.post(url, json=body).status_code == 503


async def test_drift_report_files_a_report_with_sample_evidence(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    response = authenticated_client.post(
        DRIFT_URL,
        json={
            "model_name": "pcb_body_defect",
            "description": "Elevated REVIEW_REQUIRED rate on Body samples this run",
            "samples": [_completed_sample()],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_name"] == "pcb_body_defect"
    assert body["model_version"] == "JcProg/body@v2"  # derived from the sample, none was given
    assert body["status"] == "open"

    (report,) = (await db_async_session.scalars(select(DriftReport))).all()
    assert report.stats == {"source": "workflow", "sample_ids": ["S1"]}


async def test_drift_report_honors_an_explicit_model_version(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    response = authenticated_client.post(
        DRIFT_URL,
        json={
            "model_name": "pcb_body_defect",
            "description": "x",
            "model_version": "JcProg/body@v3",
            "samples": [_completed_sample()],
        },
    )

    assert response.json()["model_version"] == "JcProg/body@v3"


async def test_drift_report_needs_no_samples(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    response = authenticated_client.post(
        DRIFT_URL, json={"model_name": "pcb_body_defect", "description": "x"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["model_version"] is None


async def test_flagging_creates_a_ticket_with_no_case_and_the_real_model_name(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    response = authenticated_client.post(
        TICKETS_URL,
        json={
            "tickets": [
                {
                    "sample": _completed_sample(),
                    "reason": "machine flagged a false positive",
                    "correct_label": "Golden",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    (ticket_out,) = response.json()
    assert ticket_out["sample_ref"] == "S1"
    assert ticket_out["model_name"] == "pcb_body_defect"  # the real name, not "body"
    assert ticket_out["status"] == "open"

    (ticket,) = (await db_async_session.scalars(select(RetrainingTicket))).all()
    assert ticket.case_id is None
    assert ticket.sample_ref == "S1"
    assert ticket.model_version == "JcProg/body@v2"
    assert ticket.observed_label == "MissingPart"
    assert ticket.correct_label == "Golden"


async def test_flagging_several_samples_creates_one_ticket_each(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    response = authenticated_client.post(
        TICKETS_URL,
        json={
            "tickets": [
                {"sample": _completed_sample(sample_id="S1"), "reason": "a"},
                {"sample": _completed_sample(sample_id="S3"), "reason": "b"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert {t["sample_ref"] for t in response.json()} == {"S1", "S3"}
    tickets = (await db_async_session.scalars(select(RetrainingTicket))).all()
    assert len(tickets) == 2


async def test_flagging_is_all_or_nothing_when_a_sample_never_reached_routing(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    response = authenticated_client.post(
        TICKETS_URL,
        json={
            "tickets": [
                {"sample": _completed_sample(), "reason": "a"},
                {"sample": _stage_one_only_sample(), "reason": "b"},
            ]
        },
    )

    assert response.status_code == 422
    assert "S2" in response.json()["detail"]
    assert (await db_async_session.scalars(select(RetrainingTicket))).all() == []


async def test_flagging_requires_a_reason(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        TICKETS_URL, json={"tickets": [{"sample": _completed_sample(), "reason": ""}]}
    )

    assert response.status_code == 422
