"""Retraining jobs: the JobManager queue itself (with controllable fake trainers, so states are
deterministic) and the /jobs HTTP surface on top of it (with the real StubTrainer, made fast)."""

import threading
import time
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from inference_service.jobs import JobManager
from inference_service.main import app, get_jobs
from inference_service.schemas import JobSample, JobState
from inference_service.trainer import (
    StubTrainer,
    TrainingCancelled,
    TrainingOutcome,
    TrainingRequest,
)

SAMPLES = [JobSample(case_id="case-1", observed_label="a", expected_label="b")]
BODY = {
    "model": "classifier_1",
    "client_ref": "job-1",
    "samples": [{"case_id": "case-1", "observed_label": "a", "expected_label": "b"}],
}


def wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


class GatedTrainer:
    """Runs until `release` is set (or cancelled), so a test can hold a job in `running`."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def train(
        self,
        request: TrainingRequest,
        progress: Callable[[float], None],
        cancelled: threading.Event,
    ) -> TrainingOutcome:
        self.started.set()
        while not self.release.is_set():
            if cancelled.wait(0.01):
                raise TrainingCancelled
        return TrainingOutcome("repo", "rev", simulated=False)


class ExplodingTrainer:
    def train(
        self,
        request: TrainingRequest,
        progress: Callable[[float], None],
        cancelled: threading.Event,
    ) -> TrainingOutcome:
        raise RuntimeError("out of memory")


def _submit(manager: JobManager, ref: str = "job-1") -> str:
    return manager.submit(
        model="classifier_1",
        client_ref=ref,
        base_version="JcProg/example@main",
        samples=SAMPLES,
        notes=None,
    ).id


@pytest.fixture
def manager() -> Iterator[JobManager]:
    m = JobManager(StubTrainer(duration_seconds=0.05, steps=5))
    yield m
    m.shutdown()


def test_job_runs_to_success_with_a_simulated_result(manager: JobManager) -> None:
    job_id = _submit(manager)

    def done() -> bool:
        job = manager.get(job_id)
        return job is not None and job.status is JobState.SUCCEEDED

    wait_for(done)
    job = manager.get(job_id)
    assert job is not None
    assert job.progress == 1.0
    assert job.started_at is not None and job.finished_at is not None
    assert job.result is not None
    assert job.result.simulated is True
    # the stub "produces" the version it started from - no new weights
    assert (job.result.artifact.repo_id, job.result.artifact.revision) == ("JcProg/example", "main")
    assert job.result.metrics == {"samples": 1.0}


def test_resubmitting_a_client_ref_returns_the_same_job(manager: JobManager) -> None:
    first = _submit(manager, "same-ref")
    second = _submit(manager, "same-ref")

    assert first == second
    assert len(manager.list_jobs()) == 1


def test_jobs_run_one_at_a_time_in_order() -> None:
    trainer = GatedTrainer()
    manager = JobManager(trainer)
    try:
        first = _submit(manager, "a")
        second = _submit(manager, "b")
        trainer.started.wait(timeout=5)

        first_job, second_job = manager.get(first), manager.get(second)
        assert first_job is not None and second_job is not None
        assert first_job.status is JobState.RUNNING
        assert second_job.status is JobState.QUEUED

        trainer.release.set()
        wait_for(lambda: (j := manager.get(second)) is not None and j.status is JobState.SUCCEEDED)
    finally:
        manager.shutdown()


def test_cancelling_a_queued_job_skips_it() -> None:
    trainer = GatedTrainer()
    manager = JobManager(trainer)
    try:
        _submit(manager, "a")
        queued = _submit(manager, "b")
        trainer.started.wait(timeout=5)

        cancelled = manager.cancel(queued)
        assert cancelled is not None and cancelled.status is JobState.CANCELLED

        trainer.release.set()
        wait_for(lambda: (j := manager.get(queued)) is not None and j.finished_at is not None)
        job = manager.get(queued)
        assert job is not None and job.status is JobState.CANCELLED
        assert job.started_at is None  # it never ran
    finally:
        manager.shutdown()


def test_cancelling_a_running_job_stops_it() -> None:
    trainer = GatedTrainer()
    manager = JobManager(trainer)
    try:
        job_id = _submit(manager)
        trainer.started.wait(timeout=5)

        manager.cancel(job_id)

        wait_for(lambda: (j := manager.get(job_id)) is not None and j.status is JobState.CANCELLED)
    finally:
        manager.shutdown()


def test_a_trainer_exception_fails_the_job_but_not_the_worker() -> None:
    manager = JobManager(ExplodingTrainer())
    try:
        first = _submit(manager, "a")
        wait_for(lambda: (j := manager.get(first)) is not None and j.status is JobState.FAILED)
        job = manager.get(first)
        assert job is not None and job.error == "out of memory"

        second = _submit(manager, "b")  # the worker is still alive to pick this up
        wait_for(lambda: (j := manager.get(second)) is not None and j.status is JobState.FAILED)
    finally:
        manager.shutdown()


def test_history_limit_evicts_the_oldest_finished_jobs() -> None:
    manager = JobManager(StubTrainer(duration_seconds=0.0, steps=1), history_limit=2)
    try:
        ids = []
        for n in range(3):
            ids.append(_submit(manager, f"ref-{n}"))
            wait_for(lambda: (j := manager.get(ids[-1])) is not None and j.finished_at is not None)

        _submit(manager, "ref-3")  # pushes past the limit; the oldest finished ones go

        assert manager.get(ids[0]) is None
        assert manager.get(ids[2]) is not None
    finally:
        manager.shutdown()


@pytest.fixture
def api_client(client: TestClient, manager: JobManager) -> Iterator[TestClient]:
    app.dependency_overrides[get_jobs] = lambda: manager
    yield client
    app.dependency_overrides.pop(get_jobs, None)


def test_submit_returns_202_and_defaults_the_base_version_to_the_live_one(
    api_client: TestClient,
) -> None:
    response = api_client.post("/jobs", json=BODY)

    assert response.status_code == 202
    body = response.json()
    assert body["client_ref"] == "job-1"
    assert body["model"] == "classifier_1"
    assert body["base_version"] == "JcProg/example@main"
    assert body["sample_count"] == 1
    assert body["status"] in {"queued", "running", "succeeded"}


def test_job_can_be_polled_to_completion(api_client: TestClient) -> None:
    job_id = api_client.post("/jobs", json=BODY).json()["id"]

    wait_for(lambda: api_client.get(f"/jobs/{job_id}").json()["status"] == "succeeded")

    assert api_client.get(f"/jobs/{job_id}").json()["result"]["simulated"] is True
    assert [j["id"] for j in api_client.get("/jobs").json()] == [job_id]


def test_submit_for_an_unknown_model_is_404(api_client: TestClient) -> None:
    response = api_client.post("/jobs", json={**BODY, "model": "nope"})
    assert response.status_code == 404


def test_submit_requires_samples(api_client: TestClient) -> None:
    assert api_client.post("/jobs", json={**BODY, "samples": []}).status_code == 422


def test_unknown_job_is_404(api_client: TestClient) -> None:
    assert api_client.get("/jobs/nope").status_code == 404
    assert api_client.post("/jobs/nope/cancel").status_code == 404


def test_cancelling_a_finished_job_is_409(api_client: TestClient) -> None:
    job_id = api_client.post("/jobs", json=BODY).json()["id"]
    wait_for(lambda: api_client.get(f"/jobs/{job_id}").json()["status"] == "succeeded")

    assert api_client.post(f"/jobs/{job_id}/cancel").status_code == 409
