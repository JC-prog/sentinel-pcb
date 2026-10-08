"""Covers the Work tab's Agent 2 review + human-in-the-loop decision routes
(POST /api/orchestrator/reviews/run, PUT .../reviews/decision, GET .../reviews/decisions) and the
pure helpers in app/workflow/services/reviews.py. The LangGraph pipeline itself is stubbed - it
needs images, Ollama and (optionally) OpenAI; these tests are about what the web layer does with
its output.
"""

import json
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db.models import WorkflowReviewDecision
from app.shared.modelops.run_drift import normalize_defect
from app.workflow.services import reviews, streaming

RUN_URL = "/api/orchestrator/reviews/run"
DECISION_URL = "/api/orchestrator/reviews/decision"
DECISIONS_URL = "/api/orchestrator/reviews/decisions"


def _sample(sample_id: str = "S1") -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "board": "Board1",
        "component": "C978",
        "defect_image": f"/abs/{sample_id}_defect.jpg",
        "golden_image": f"/abs/{sample_id}_golden.jpg",
        "failed_inspections": {"Body": {"status": "Failed"}},
    }


def _result(sample_id: str = "S1", prediction: str = "WrongPart") -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "final_decision": "REVIEW_REQUIRED",
        "feature_classification": {"prediction": "Body", "confidence": 0.95},
        "defect_classification": {"prediction": prediction, "confidence": 0.6},
    }


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    reviews._cases.clear()
    reviews._reviews.clear()
    yield
    reviews._cases.clear()
    reviews._reviews.clear()


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch, output: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return output

    monkeypatch.setattr(reviews, "_execute_pipeline", lambda: execute)
    return calls


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("MissingPart", "missing part"),
        ("WrongPart_13", "wrong part"),
        ("Shift", "shifted"),
        ("shifted", "shifted"),
        ("SolderInsufficient", "solder insufficient"),
        ("Golden", "no defect"),
        ("No Defect / Pass", "no defect / pass"),
        (None, "no defect"),
    ],
)
def test_normalize_defect(label: str | None, expected: str) -> None:
    assert normalize_defect(label) == expected


def test_register_run_keeps_every_review_required_sample_but_nothing_else() -> None:
    accepted = {**_result("S2"), "final_decision": "ACCEPTED"}
    stage_one_only = {
        "sample_id": "S3",
        "final_decision": "REVIEW_REQUIRED",
        "details": {"feature_classification": {"prediction": "Text1", "confidence": 0.2}},
    }
    registered = reviews.register_run(
        "run-a",
        [_sample("S1"), _sample("S2"), {**_sample("S3"), "machine_defect": "WrongPart_13"}],
        [_result("S1"), accepted, stage_one_only],
    )

    assert registered == 2
    assert list(reviews._cases) == [("run-a", "S1"), ("run-a", "S3")]
    agent2_input = reviews._cases[("run-a", "S1")]
    assert agent2_input["preliminary_defect"] == "WrongPart"
    assert agent2_input["defect_image_path"] == "/abs/S1_defect.jpg"
    assert agent2_input["feature_type"] == "Body"


def test_a_sample_that_stopped_at_feature_classification_falls_back_to_the_machine_defect() -> None:
    stage_one_only = {
        "sample_id": "S3",
        "final_decision": "REVIEW_REQUIRED",
        "details": {"feature_classification": {"prediction": "Text", "confidence": 0.2}},
    }
    sample = {**_sample("S3"), "machine_defect": "WrongPart_13", "source_feature": "Text1"}

    agent2_input = reviews.build_agent2_input(sample, stage_one_only)

    assert agent2_input["preliminary_defect"] == "WrongPart_13"
    assert normalize_defect(agent2_input["preliminary_defect"]) == "wrong part"
    assert agent2_input["feature_type"] == "Text"
    assert agent2_input["confidence"] == 0.0
    # no stage-1 result at all: the dataset's own feature label is used, not a made-up "Body"
    assert reviews.build_agent2_input(sample, {"sample_id": "S3"})["feature_type"] == "Text1"


def test_review_routes_require_login(client: TestClient) -> None:
    assert client.post(RUN_URL, json={"run_id": "r", "sample_id": "S1"}).status_code == 401
    assert client.get(DECISIONS_URL, params={"run_id": "r"}).status_code == 401


@pytest.mark.parametrize(
    "setting_name", ["orchestrator_agent_enabled", "explainability_review_agent_enabled"]
)
def test_review_routes_require_both_kill_switches(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch, setting_name: str
) -> None:
    monkeypatch.setattr(settings, setting_name, False)
    response = authenticated_client.post(RUN_URL, json={"run_id": "r", "sample_id": "S1"})
    assert response.status_code == 503


def test_run_review_unknown_case_is_404(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(RUN_URL, json={"run_id": "nope", "sample_id": "S1"})
    assert response.status_code == 404
    assert "Rerun" in response.json()["detail"]


def test_run_review_flags_a_conflict_when_the_agents_disagree(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviews.register_run("run-a", [_sample()], [_result(prediction="WrongPart")])
    calls = _stub_pipeline(
        monkeypatch,
        {
            "predicted_defect": "missing part",
            "final_confidence": 0.91,
            "diagnosis": "Laser height is ~0 - the body is absent.",
            "self_check_passed": True,
            "contradiction_detected": False,
            "ipc_citations": ["IPC-A-610 Section 8.3.1"],
            "visual_evidence": "bare pads",
            "errors": [],
        },
    )

    response = authenticated_client.post(RUN_URL, json={"run_id": "run-a", "sample_id": "S1"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent1_verdict"] == "wrong part"
    assert body["agent2_verdict"] == "missing part"
    assert body["conflict"] is True
    assert body["ipc_citations"] == ["IPC-A-610 Section 8.3.1"]
    assert calls[0]["component_ref"] == "C978"


def test_run_review_agreement_is_not_a_conflict(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviews.register_run("run-a", [_sample()], [_result(prediction="MissingPart")])
    _stub_pipeline(
        monkeypatch,
        {"predicted_defect": "missing part", "self_check_passed": True, "diagnosis": "ok"},
    )

    body = authenticated_client.post(RUN_URL, json={"run_id": "run-a", "sample_id": "S1"}).json()

    assert body["conflict"] is False


def test_run_review_failed_self_check_is_a_conflict_even_when_verdicts_match(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviews.register_run("run-a", [_sample()], [_result(prediction="MissingPart")])
    _stub_pipeline(
        monkeypatch,
        {"predicted_defect": "missing part", "self_check_passed": False, "diagnosis": "?"},
    )

    body = authenticated_client.post(RUN_URL, json={"run_id": "run-a", "sample_id": "S1"}).json()

    assert body["conflict"] is True


async def test_decision_is_saved_then_replaced(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    payload = {
        "run_id": "run-a",
        "sample_id": "S1",
        "selected_source": "AI",
        "final_result": "missing part",
        "machine_result": "wrong part",
        "ai_result": "missing part",
        "operator_notes": " bare pads confirmed ",
    }
    first = authenticated_client.put(DECISION_URL, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["operator_notes"] == "bare pads confirmed"

    second = authenticated_client.put(
        DECISION_URL,
        json={**payload, "selected_source": "MANUAL", "final_result": "Tombstone"},
    )
    assert second.status_code == 200, second.text

    (row,) = (await db_async_session.scalars(select(WorkflowReviewDecision))).all()
    assert (row.selected_source, row.final_result) == ("MANUAL", "Tombstone")

    listed = authenticated_client.get(DECISIONS_URL, params={"run_id": "run-a"}).json()
    assert [d["sample_id"] for d in listed] == ["S1"]
    assert authenticated_client.get(DECISIONS_URL, params={"run_id": "other"}).json() == []


def test_decision_rejects_an_unknown_source(authenticated_client: TestClient) -> None:
    response = authenticated_client.put(
        DECISION_URL,
        json={"run_id": "r", "sample_id": "S1", "selected_source": "GUESS", "final_result": "x"},
    )
    assert response.status_code == 422


def test_decision_rejects_a_blank_result(authenticated_client: TestClient) -> None:
    response = authenticated_client.put(
        DECISION_URL,
        json={"run_id": "r", "sample_id": "S1", "selected_source": "AI", "final_result": ""},
    )
    assert response.status_code == 422


CASES_URL = "/api/orchestrator/reviews/cases"
IMAGE_URL = "/api/orchestrator/reviews/image"


def _frames(raw: list[str]) -> list[tuple[str, dict[str, Any]]]:
    parsed = []
    for frame in raw:
        event_line, data_line = frame.strip().split("\n", 1)
        parsed.append(
            (event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: ")))
        )
    return parsed


async def test_auto_review_dispatches_every_registered_sample_and_approves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviews.register_run(
        "run-a",
        [_sample("S1"), _sample("S2")],
        [_result("S1", "WrongPart"), _result("S2", "MissingPart")],
    )
    verdicts = iter(["missing part", "missing part"])
    _stub_pipeline_factory(
        monkeypatch,
        lambda: {"predicted_defect": next(verdicts), "self_check_passed": True, "diagnosis": "d"},
    )

    frames = _frames([f async for f in streaming._auto_review("run-a")])

    review_events = [data for event, data in frames if event == "review"]
    assert [r["sample_id"] for r in review_events] == ["S1", "S2"]
    assert [r["conflict"] for r in review_events] == [True, False]
    logs = "\n".join(d["text"] for e, d in frames if e == "log")
    assert "Escalating 2 sample(s)" in logs
    assert "[Review Required] S1" in logs
    assert "S2: explanation ready; agents agree on missing part" in logs


async def test_auto_review_keeps_going_when_one_sample_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviews.register_run("run-a", [_sample("S1"), _sample("S2")], [_result("S1"), _result("S2")])
    calls: Iterator[Exception | dict[str, Any]] = iter(
        [RuntimeError("vlm down"), {"predicted_defect": "wrong part", "self_check_passed": True}]
    )

    def outcome() -> dict[str, Any]:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    _stub_pipeline_factory(monkeypatch, outcome)

    frames = _frames([f async for f in streaming._auto_review("run-a")])

    logs = "\n".join(d["text"] for e, d in frames if e == "log")
    assert "[Agent 2 Error] Sample S1: RuntimeError: vlm down" in logs
    assert [d["sample_id"] for e, d in frames if e == "review"] == ["S2"]


def _stub_pipeline_factory(
    monkeypatch: pytest.MonkeyPatch, outcome: Callable[[], dict[str, Any]]
) -> None:
    monkeypatch.setattr(reviews, "_execute_pipeline", lambda: lambda payload: outcome())


async def test_cases_list_merges_review_and_decision(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviews.register_run("run-a", [_sample("S1"), _sample("S2")], [_result("S1"), _result("S2")])
    _stub_pipeline(monkeypatch, {"predicted_defect": "missing part", "self_check_passed": True})
    authenticated_client.post(RUN_URL, json={"run_id": "run-a", "sample_id": "S1"})
    authenticated_client.put(
        DECISION_URL,
        json={
            "run_id": "run-a",
            "sample_id": "S1",
            "selected_source": "AI",
            "final_result": "missing part",
        },
    )

    cases = authenticated_client.get(CASES_URL, params={"run_id": "run-a"}).json()

    assert [c["sample_id"] for c in cases] == ["S1", "S2"]
    assert cases[0]["review"]["agent2_verdict"] == "missing part"
    assert cases[0]["decision"]["final_result"] == "missing part"
    assert cases[1]["review"] is None and cases[1]["decision"] is None
    assert cases[0]["component_ref"] == "C978" and cases[0]["agent1_verdict"] == "wrong part"
    assert authenticated_client.get(CASES_URL, params={"run_id": "other"}).json() == []


def test_image_route_serves_only_registered_files(authenticated_client: TestClient) -> None:
    tmp_dir = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp_dir.name)
    defect = tmp_path / "defect.jpg"
    defect.write_bytes(b"\xff\xd8jpeg")
    sample = {**_sample(), "defect_image": str(defect), "golden_image": str(tmp_path / "gone.jpg")}
    reviews.register_run("run-a", [sample], [_result()])

    ok = authenticated_client.get(
        IMAGE_URL, params={"run_id": "run-a", "sample_id": "S1", "kind": "defect"}
    )
    assert ok.status_code == 200 and ok.content == b"\xff\xd8jpeg"

    missing_file = authenticated_client.get(
        IMAGE_URL, params={"run_id": "run-a", "sample_id": "S1", "kind": "golden"}
    )
    assert missing_file.status_code == 404
    unknown = authenticated_client.get(
        IMAGE_URL, params={"run_id": "x", "sample_id": "S1", "kind": "defect"}
    )
    assert unknown.status_code == 404
    bad_kind = authenticated_client.get(
        IMAGE_URL, params={"run_id": "run-a", "sample_id": "S1", "kind": "../../etc/passwd"}
    )
    assert bad_kind.status_code == 422
    tmp_dir.cleanup()
