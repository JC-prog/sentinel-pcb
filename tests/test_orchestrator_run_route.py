"""Covers POST /api/orchestrator/run/stream (app/main.py) - the SSE endpoint the Work tab drives.
Mirrors tests/test_chat.py's SSE-parsing approach and tests/test_explainability_review_route.py's
gate-checking style (401/503/403), plus an end-to-end "prepare" run against small fixture files -
"prepare" alone doesn't touch the inference microservice, so it needs no mocking to exercise the
real dataset_preparation service through the whole HTTP round trip.
"""

import json
import shutil
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings

_INSPECTION_XML = """<?xml version="1.0"?>
<Boards>
  <Board Name="B1">
    <Component Name="R131" Package="SOT-23">
      <Feature Identifier="Text" FeatureStatus="Failed">
        <FeatureResult>
          <Inspection Type="AI2" status="Failed" FailedInspectionCriterias="Blob">
            <Measurements>
              <Measurement Value="1.0" Minimum="0.5" Maximum="2.0" />
            </Measurements>
          </Inspection>
        </FeatureResult>
      </Feature>
    </Component>
  </Board>
</Boards>
"""

_DATASET_CSV = (
    "SampleID,Board,Package,Component,InspectionDefinition,Feature,Defect,GoldenImage,DefectImage\n"
    "S1,B1,SOT-23,R131,AI2,Text,WrongPart,missing_golden.jpg,missing_defect.jpg\n"
)


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    frames = [f for f in body.split("\n\n") if f.strip()]
    parsed = []
    for frame in frames:
        event = "message"
        data = "{}"
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
        parsed.append((event, json.loads(data)))
    return parsed


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Generator[None, None, None]:
    yield
    shutil.rmtree(settings.orchestrator_data_dir, ignore_errors=True)


def _upload_dataset_and_xml(client: TestClient) -> tuple[str, str]:
    dataset = client.post(
        "/api/orchestrator/uploads/dataset",
        files={"file": ("dataset.csv", _DATASET_CSV.encode(), "text/csv")},
    )
    assert dataset.status_code == 200, dataset.text
    xml = client.post(
        "/api/orchestrator/uploads/xml",
        files={"file": ("inspection.xml", _INSPECTION_XML.encode(), "application/xml")},
    )
    assert xml.status_code == 200, xml.text
    return dataset.json()["id"], xml.json()["id"]


def test_run_requires_login(client: TestClient) -> None:
    response = client.post(
        "/api/orchestrator/run/stream",
        json={"mode": "prepare", "dataset_id": "x", "xml_id": "y"},
    )
    assert response.status_code == 401


def test_run_returns_503_when_disabled(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "orchestrator_agent_enabled", False)
    dataset_id, xml_id = "unused", "unused"
    response = authenticated_client.post(
        "/api/orchestrator/run/stream",
        json={"mode": "prepare", "dataset_id": dataset_id, "xml_id": xml_id},
    )
    assert response.status_code == 503


def test_run_reports_missing_uploads_as_an_sse_error(authenticated_client: TestClient) -> None:
    with authenticated_client.stream(
        "POST",
        "/api/orchestrator/run/stream",
        json={"mode": "prepare", "dataset_id": "does-not-exist", "xml_id": "does-not-exist"},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200, body

    frames = _parse_sse(body)
    assert frames[0][0] == "error"
    assert frames[-1] == ("done", {})


def test_prepare_run_streams_status_and_result(authenticated_client: TestClient) -> None:
    dataset_id, xml_id = _upload_dataset_and_xml(authenticated_client)

    with authenticated_client.stream(
        "POST",
        "/api/orchestrator/run/stream",
        json={"mode": "prepare", "dataset_id": dataset_id, "xml_id": xml_id},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200, body

    frames = _parse_sse(body)
    events = [event for event, _ in frames]
    assert events == ["log", "status", "log", "result", "done"]

    status_data = frames[1][1]
    assert status_data["input_samples"] == 1
    # The sample's feature/inspection match the XML fixture, so preparation succeeds even though
    # the referenced image files don't exist on disk (that's a verification-stage concern).
    assert status_data["preparation_ready"] == 1
