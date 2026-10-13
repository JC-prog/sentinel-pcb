"""In-memory retraining job queue: one worker thread runs jobs one at a time, in submission order
(training is heavy - two at once would just fight over the same CPU/GPU). State lives only in this
process by design; the backend's database is the durable record and reconciles against GET /jobs.
"""

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from inference_service.schemas import (
    JobArtifact,
    JobResponse,
    JobResult,
    JobSample,
    JobState,
)
from inference_service.trainer import (
    Trainer,
    TrainingCancelled,
    TrainingOutcome,
    TrainingRequest,
)

logger = logging.getLogger(__name__)

_FINISHED = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Job:
    id: str
    client_ref: str
    model: str
    base_version: str
    samples: list[JobSample]
    notes: str | None
    created_at: datetime = field(default_factory=_now)
    status: JobState = JobState.QUEUED
    progress: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    outcome: TrainingOutcome | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)


class JobManager:
    def __init__(self, trainer: Trainer, history_limit: int = 200) -> None:
        self._trainer = trainer
        self._history_limit = history_limit
        self._jobs: dict[str, _Job] = {}
        self._by_client_ref: dict[str, str] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def submit(
        self,
        *,
        model: str,
        client_ref: str,
        base_version: str,
        samples: list[JobSample],
        notes: str | None,
    ) -> JobResponse:
        with self._lock:
            existing = self._jobs.get(self._by_client_ref.get(client_ref, ""))
            if existing is not None:
                return self._snapshot(existing)

            job = _Job(
                id=uuid.uuid4().hex,
                client_ref=client_ref,
                model=model,
                base_version=base_version,
                samples=samples,
                notes=notes,
            )
            self._jobs[job.id] = job
            self._by_client_ref[client_ref] = job.id
            self._evict_finished()
            self._ensure_worker()
            self._queue.put(job.id)
            return self._snapshot(job)

    def get(self, job_id: str) -> JobResponse | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._snapshot(job) if job is not None else None

    def list_jobs(self) -> list[JobResponse]:
        with self._lock:
            return [self._snapshot(j) for j in self._jobs.values()]

    def cancel(self, job_id: str) -> JobResponse | None:
        """Cancels a queued job outright, or asks a running one to stop (it flips to `cancelled`
        once the trainer notices). A finished job is returned unchanged - callers check `status`."""

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status is JobState.QUEUED:
                job.status = JobState.CANCELLED
                job.finished_at = _now()
                job.cancelled.set()
            elif job.status is JobState.RUNNING:
                job.cancelled.set()
            return self._snapshot(job)

    def shutdown(self) -> None:
        with self._lock:
            for job in self._jobs.values():
                if job.status in (JobState.QUEUED, JobState.RUNNING):
                    job.cancelled.set()
            worker = self._worker
        self._queue.put(None)
        if worker is not None:
            worker.join(timeout=5)

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="retraining-worker", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.status is not JobState.QUEUED:
                    continue  # cancelled while waiting, or evicted
                job.status = JobState.RUNNING
                job.started_at = _now()
            self._execute(job)

    def _execute(self, job: _Job) -> None:
        def progress(value: float) -> None:
            with self._lock:
                job.progress = max(0.0, min(1.0, value))

        request = TrainingRequest(
            model=job.model,
            base_version=job.base_version,
            samples=job.samples,
            notes=job.notes,
        )
        try:
            outcome = self._trainer.train(request, progress, job.cancelled)
        except TrainingCancelled:
            self._finish(job, JobState.CANCELLED)
        except Exception as exc:
            logger.exception("retraining job %s failed", job.id)
            self._finish(job, JobState.FAILED, error=str(exc) or type(exc).__name__)
        else:
            self._finish(job, JobState.SUCCEEDED, outcome=outcome)

    def _finish(
        self,
        job: _Job,
        status: JobState,
        *,
        error: str | None = None,
        outcome: TrainingOutcome | None = None,
    ) -> None:
        with self._lock:
            job.status = status
            job.error = error
            job.outcome = outcome
            job.finished_at = _now()
            if status is JobState.SUCCEEDED:
                job.progress = 1.0
        logger.info("retraining job %s %s", job.id, status.value)

    def _evict_finished(self) -> None:
        """Called with the lock held: keep memory bounded by dropping the oldest finished jobs
        once there are more than `history_limit`. Unfinished jobs are never dropped."""

        excess = len(self._jobs) - self._history_limit
        if excess <= 0:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.status in _FINISHED),
            key=lambda j: j.created_at,
        )
        for job in finished[:excess]:
            del self._jobs[job.id]
            self._by_client_ref.pop(job.client_ref, None)

    @staticmethod
    def _snapshot(job: _Job) -> JobResponse:
        result = None
        if job.outcome is not None:
            result = JobResult(
                simulated=job.outcome.simulated,
                artifact=JobArtifact(
                    repo_id=job.outcome.artifact_repo_id,
                    revision=job.outcome.artifact_revision,
                ),
                metrics=job.outcome.metrics,
            )
        return JobResponse(
            id=job.id,
            client_ref=job.client_ref,
            model=job.model,
            base_version=job.base_version,
            status=job.status,
            progress=job.progress,
            sample_count=len(job.samples),
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error=job.error,
            result=result,
        )
