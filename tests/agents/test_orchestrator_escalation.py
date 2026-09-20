"""Tests for orchestrator_agent's escalation hand-off to explainability_review_agent (the
Work-tab-only agent ported as-is from pcb_agentic_inspector's Agent 2) - added to
OrchestratorAgent._execute_inference/_escalate_review in app/agents/orchestrator_agent/orchestrator.py.
Mirrors adc_inspection_agent's equivalent escalation to case_review_agent for the chat pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agents.orchestrator_agent.orchestrator import OrchestratorAgent
from app.agents.orchestrator_agent.services.common import ServiceResult
from app.config.settings import settings

# orchestrator_agent/ is excluded from strict typing (pyproject.toml) as a close, deliberately
# unannotated port - calling into it from this typed test file needs no-untyped-call silenced at
# each call site, same idea as test_orchestrator_policy.py's typeddict-item ignores elsewhere.


def _agent() -> OrchestratorAgent:
    return OrchestratorAgent(username="tester")  # type: ignore[no-untyped-call]


def _sample(**overrides: Any) -> dict[str, Any]:
    base = {
        "sample_id": "S1",
        "board": "B1",
        "component": "C978",
        "defect_image": "defect.jpg",
        "golden_image": "golden.jpg",
        "failed_inspections": {"BlobDetection": {"side_overhang_percent": 62.0}},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_escalate_review_skipped_when_agent_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "explainability_review_agent_enabled", False)
    result = await _agent()._escalate_review(_sample(), {})  # type: ignore[no-untyped-call]
    assert result == {"skipped": True, "reason": "explainability review agent disabled"}


@pytest.mark.asyncio
async def test_escalate_review_maps_sample_fields_and_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "explainability_review_agent_enabled", True)

    captured: dict[str, Any] = {}

    def _fake_execute(input_data: dict[str, Any]) -> dict[str, Any]:
        captured.update(input_data)
        return {
            "predicted_defect": "shifted",
            "final_confidence": 0.95,
            "diagnosis": "Side overhang exceeds IPC-A-610 Class 2 limit.",
            "self_check_passed": True,
            "contradiction_detected": False,
            "ipc_citations": ["IPC-A-610 Class 2 Section 8.3.2"],
            "visual_evidence": "Visible lateral shift.",
        }

    monkeypatch.setattr(
        "app.agents.explainability_review_agent.execute_explainability_review", _fake_execute
    )

    payload = {
        "feature_classification": {"prediction": "Body"},
        "defect_classification": {"prediction": "Shifted", "confidence": 0.4},
    }
    result = await _agent()._escalate_review(_sample(), payload)  # type: ignore[no-untyped-call]

    assert captured["board_id"] == "B1"
    assert captured["component_ref"] == "C978"
    assert captured["defect_image_path"] == "defect.jpg"
    assert captured["golden_image_path"] == "golden.jpg"
    assert captured["feature_type"] == "Body"
    assert captured["preliminary_defect"] == "Shifted"
    assert captured["confidence"] == 0.4
    assert captured["aoi_measurements"] == {"BlobDetection": {"side_overhang_percent": 62.0}}

    assert result["predicted_defect"] == "shifted"
    assert result["confidence"] == 0.95
    assert result["self_check_passed"] is True


@pytest.mark.asyncio
async def test_escalate_review_reads_classification_from_details_on_recoverable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "explainability_review_agent_enabled", True)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.agents.explainability_review_agent.execute_explainability_review",
        lambda input_data: captured.update(input_data) or {},
    )

    payload = {
        "status": "FEATURE_CLASSIFICATION_UNCERTAIN",
        "details": {"feature_classification": {"prediction": "Lead"}},
    }
    await _agent()._escalate_review(_sample(), payload)  # type: ignore[no-untyped-call]

    assert captured["feature_type"] == "Lead"
    assert captured["preliminary_defect"] is None


@pytest.mark.asyncio
async def test_escalate_review_skipped_on_pipeline_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "explainability_review_agent_enabled", True)

    def _raise(input_data: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("Ollama unreachable")

    monkeypatch.setattr(
        "app.agents.explainability_review_agent.execute_explainability_review", _raise
    )

    result = await _agent()._escalate_review(_sample(), {})  # type: ignore[no-untyped-call]
    assert result["skipped"] is True
    assert "Ollama unreachable" in result["reason"]


@pytest.mark.asyncio
async def test_execute_inference_only_escalates_review_required_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent()
    escalated: list[str] = []

    async def _fake_infer_sample(sample: dict[str, Any]) -> ServiceResult:
        if sample["sample_id"] == "ACCEPTED_SAMPLE":
            return ServiceResult(
                True,
                "INFERENCE_COMPLETED",
                data={
                    "sample_id": sample["sample_id"],
                    "feature_classification": {"prediction": "Body", "confidence": 0.99},
                    "defect_classification": {"prediction": "NoDefect", "confidence": 0.99},
                    "comparison": {"feature_agreement": True, "defect_agreement": True},
                },
            )
        return ServiceResult(
            False,
            "FEATURE_CLASSIFICATION_UNCERTAIN",
            data={"sample_id": sample["sample_id"]},
            recoverable=True,
        )

    async def _fake_escalate(sample: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        escalated.append(sample["sample_id"])
        return {"predicted_defect": "shifted"}

    monkeypatch.setattr(agent.inference, "infer_sample", _fake_infer_sample)
    monkeypatch.setattr(agent, "_escalate_review", _fake_escalate)

    @dataclass
    class _State:
        verified_samples: list[dict[str, Any]] = field(
            default_factory=lambda: [_sample(sample_id="ACCEPTED_SAMPLE"), _sample(sample_id="REVIEW_SAMPLE")]
        )
        inference_results: list[dict[str, Any]] = field(default_factory=list)
        inference_attempted: int = 0
        inference_completed: int = 0
        accepted: int = 0
        review_required: int = 0
        inference_aborted: int = 0
        observations: list[str] = field(default_factory=list)

    state = _State()
    await agent._execute_inference(state)  # type: ignore[no-untyped-call]

    assert escalated == ["REVIEW_SAMPLE"]
    accepted_result = next(r for r in state.inference_results if r["sample_id"] == "ACCEPTED_SAMPLE")
    review_result = next(r for r in state.inference_results if r["sample_id"] == "REVIEW_SAMPLE")
    assert "explainability_result" not in accepted_result
    assert review_result["explainability_result"] == {"predicted_defect": "shifted"}
