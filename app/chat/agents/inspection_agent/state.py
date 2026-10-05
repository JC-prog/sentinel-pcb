"""The data one inspection passes through: the request (what the caller supplies) and the run (what
each sub-agent adds to it). Plain dataclasses - the sub-agents run in a fixed order (pipeline.py),
so there is no graph state to thread."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InspectionRequest:
    """Everything the tool hands the pipeline - none of it is something the LLM supplies itself
    except the optional identifying fields, which only label the Case."""

    image_bytes: bytes
    image_name: str
    username: str
    user_id: str
    conversation_id: str
    board_id: str
    component_ref: str
    inspection_xml_bytes: bytes | None = None
    inspection_xml_id: str | None = None
    package: str | None = None
    feature: str | None = None
    issue_symptom: str | None = None


@dataclass
class Stage:
    """One classifier's answer: which model, which weights, what it said and how sure it was."""

    model: str
    model_version: str | None
    label: str
    confidence: float
    scores: dict[str, float]

    def ranked_scores(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Per-label scores, best first, as [{"label", "score"}] - so the model and the UI can name
        the runner-up without sorting a dict themselves."""

        ranked = sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)
        return [{"label": label, "score": round(score, 4)} for label, score in ranked[:limit]]


@dataclass
class InspectionRun:
    request: InspectionRequest
    observations: list[str] = field(default_factory=list)
    # Set by whichever sub-agent hit something it cannot continue past; the pipeline stops there
    # and no Case is written for a run that never produced a verdict.
    error: str | None = None
    image_quality: dict[str, Any] = field(default_factory=dict)
    # None means no inspection XML was supplied - never faked to a pass or a fail.
    measurement_validation: dict[str, Any] | None = None
    region: Stage | None = None
    region_uncertain: bool = False
    defect: Stage | None = None

    def note(self, observation: str) -> None:
        self.observations.append(observation)

    def fail(self, error: str) -> None:
        self.error = error
        self.note(error)
