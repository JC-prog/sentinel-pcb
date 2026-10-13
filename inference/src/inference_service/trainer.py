"""The retraining seam. A Trainer turns a set of flagged samples into a new model artifact; the job
queue (jobs.py) and HTTP API (main.py) only know this Protocol, so a real trainer is one new class
plus a branch in get_trainer() - nothing else changes.

Only StubTrainer exists today. It exercises the whole path (queueing, progress, cancellation,
result, version bookkeeping downstream) without training anything: it "succeeds" with the model's
*current* version as the artifact and marks the result simulated, so nobody mistakes it for a
retrained model.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from inference_service.schemas import JobSample
from inference_service.settings import Settings


class TrainingCancelled(Exception):
    """Raised by a Trainer that noticed `cancelled` was set and stopped early."""


@dataclass(frozen=True)
class TrainingRequest:
    model: str
    base_version: str  # "<repo_id>@<revision>" the retrain starts from
    samples: list[JobSample]
    notes: str | None = None


@dataclass(frozen=True)
class TrainingOutcome:
    """Where the new weights ended up: a Hugging Face repo + revision the backend can later pass
    to POST /models/{name}/activate."""

    artifact_repo_id: str
    artifact_revision: str
    simulated: bool
    metrics: dict[str, float] = field(default_factory=dict)


class Trainer(Protocol):
    def train(
        self,
        request: TrainingRequest,
        progress: Callable[[float], None],
        cancelled: threading.Event,
    ) -> TrainingOutcome:
        """Blocking; called from a worker thread. Report 0..1 via `progress`, and raise
        TrainingCancelled promptly once `cancelled` is set."""


class StubTrainer:
    def __init__(self, duration_seconds: float, steps: int = 10) -> None:
        self._duration = duration_seconds
        self._steps = max(1, steps)

    def train(
        self,
        request: TrainingRequest,
        progress: Callable[[float], None],
        cancelled: threading.Event,
    ) -> TrainingOutcome:
        for step in range(self._steps):
            # wait() instead of sleep() so a cancel interrupts the pause rather than waiting it out.
            if cancelled.wait(self._duration / self._steps):
                raise TrainingCancelled
            progress((step + 1) / self._steps)

        repo_id, _, revision = request.base_version.rpartition("@")
        return TrainingOutcome(
            artifact_repo_id=repo_id or request.base_version,
            artifact_revision=revision or "main",
            simulated=True,
            metrics={"samples": float(len(request.samples))},
        )


def get_trainer(settings: Settings) -> Trainer:
    """The one place a concrete trainer is chosen (mirrors the backend's single-factory swap
    points). Add a branch here when a real trainer exists."""

    return StubTrainer(duration_seconds=settings.stub_trainer_seconds)
