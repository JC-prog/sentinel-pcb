"""Read-only access to the Work tab's stored runs, for the chat agents.

The Work tab (app/workflow/services/run_store.py) keeps a finished run, each sample's inspection
result, Agent 2's review and the operator's decision as payload-only points in three Qdrant
collections. chat may not import workflow, so this module re-states the collection names and the
payload keys it reads (tests/chat/agents/test_sample_agent.py checks them against what the workflow
writes) and only ever reads: no collection is created, nothing is written.

Points are found by filtering on payload (`run_id`, `sample_id`, `final_decision` are keyword-indexed
by the writer) rather than by id, so the writer's id scheme stays its own business. A missing
collection means nothing has been stored yet; an unreachable Qdrant raises RunStoreUnavailable with a
message the model can pass on.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

RUNS = "adc_orchestrator_runs"
SAMPLES = "adc_inspection_results"
REVIEWS = "adc_agent2_reviews"

REVIEW_REQUIRED = "REVIEW_REQUIRED"

_PAGE = 100
_MAX_POINTS = 1000
# A run point carries the whole workflow state; the run list only needs these.
_RUN_FIELDS = ["run_id", "saved_at_utc", "status", "storage_status"]

_client: AsyncQdrantClient | None = None


class RunStoreUnavailable(Exception):
    """Qdrant could not be reached; `message` is safe to tell the user."""

    def __init__(self, message: str = "the stored Work-tab runs could not be reached right now") -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    saved_at_utc: str
    status: str


@dataclass(frozen=True)
class StoredSample:
    run: StoredRun
    point: dict[str, Any]


def set_client(client: AsyncQdrantClient | None) -> None:
    """Installs (or, with None, clears) the client every call uses - tests pass an in-memory one."""

    global _client
    _client = client


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncQdrantClient]:
    if _client is not None:
        yield _client
        return
    client = AsyncQdrantClient(url=settings.qdrant_url, timeout=10)
    try:
        yield client
    finally:
        await client.close()


def _match(**fields: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in fields.items()
        ]
    )


async def _scroll(
    collection: str,
    scroll_filter: models.Filter | None,
    *,
    fields: list[str] | None = None,
    limit: int = _MAX_POINTS,
) -> list[dict[str, Any]]:
    """Up to `limit` payloads of `collection` matching the filter ([] when it does not exist)."""

    try:
        async with _connection() as client:
            if not await client.collection_exists(collection):
                return []
            payloads: list[dict[str, Any]] = []
            offset: Any = None
            while len(payloads) < limit:
                points, offset = await client.scroll(
                    collection,
                    scroll_filter=scroll_filter,
                    limit=min(_PAGE, limit - len(payloads)),
                    offset=offset,
                    with_payload=models.PayloadSelectorInclude(include=fields) if fields else True,
                    with_vectors=False,
                )
                payloads.extend(dict(point.payload or {}) for point in points)
                if offset is None:
                    break
            return payloads
    except Exception as exc:
        logger.warning("run samples: qdrant read of %s failed: %s", collection, exc)
        raise RunStoreUnavailable() from exc


def _stored_run(payload: dict[str, Any]) -> StoredRun:
    return StoredRun(
        run_id=str(payload["run_id"]),
        saved_at_utc=str(payload.get("saved_at_utc") or ""),
        status=str(payload.get("status") or ""),
    )


async def ready_runs(run_ids: list[str] | None = None) -> list[StoredRun]:
    """READY runs (a run still being written is not yet readable), newest first."""

    scroll_filter = (
        models.Filter(
            must=[models.FieldCondition(key="run_id", match=models.MatchAny(any=run_ids))]
        )
        if run_ids is not None
        else None
    )
    payloads = await _scroll(RUNS, scroll_filter, fields=_RUN_FIELDS)
    runs = [_stored_run(p) for p in payloads if p.get("storage_status") == "READY"]
    return sorted(runs, key=lambda run: run.saved_at_utc, reverse=True)


async def latest_run() -> StoredRun | None:
    runs = await ready_runs()
    return runs[0] if runs else None


async def find_samples(sample_id: str, run_id: str | None = None) -> list[StoredSample]:
    """Every stored copy of `sample_id` (one per run it appeared in), newest run first. With
    `run_id`, only that run's."""

    fields = {"sample_id": sample_id, **({"run_id": run_id} if run_id else {})}
    points = await _scroll(SAMPLES, _match(**fields))
    if not points:
        return []
    runs = {r.run_id: r for r in await ready_runs(sorted({str(p["run_id"]) for p in points}))}
    found = [StoredSample(runs[str(p["run_id"])], p) for p in points if str(p["run_id"]) in runs]
    return sorted(found, key=lambda s: s.run.saved_at_utc, reverse=True)


async def review_case_points(run_id: str) -> list[dict[str, Any]]:
    """The run's REVIEW_REQUIRED sample points (`sample` + `inference`)."""

    return await _scroll(SAMPLES, _match(run_id=run_id, final_decision=REVIEW_REQUIRED))


async def reviews_for_run(run_id: str) -> list[dict[str, Any]]:
    """The run's review points: Agent 2's output and/or the operator's decision, per sample."""

    return await _scroll(REVIEWS, _match(run_id=run_id))


async def get_review(run_id: str, sample_id: str) -> dict[str, Any] | None:
    points = await _scroll(REVIEWS, _match(run_id=run_id, sample_id=sample_id), limit=1)
    return points[0] if points else None
