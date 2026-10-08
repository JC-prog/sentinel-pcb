"""Durable record of a Work-tab run: the three payload-only Qdrant collections the source project's
Data API kept (`adc_orchestrator_runs`, `adc_inspection_results`, `adc_agent2_reviews` - see
adc_shared/repository.py), used here in-process instead of through its separate :8000 service.

The ported `Repository` is synchronous and single-writer by design (one RLock), so this module owns
exactly one instance per process and runs every call in a worker thread. Qdrant is this app's
existing Docker instance (`settings.qdrant_url`, the same one chat memory uses) - the collections
hold payloads only (no vectors), so they never collide with chat's.

Everything here degrades gracefully: when Qdrant can't be reached (or a payload is rejected) the
helper raises `StoreUnavailable`, and the callers in streaming.py / reviews.py keep working from
their in-memory copy - a run never fails because it couldn't be persisted. `Conflict`/`KeyError`/
`ValueError` from the repository are its own business rules (immutable first review, unknown
run/sample, invalid result) and pass through unchanged.
"""

import asyncio
import functools
import importlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from qdrant_client import QdrantClient, models

from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

# adc_shared/ is part of the ported, untyped drop-in (see pyproject.toml's mypy exclude), so it is
# loaded by dotted path instead of imported statically - the same way reviews.py reaches the
# explainability pipeline - and its Repository is typed Any here.
_adc = importlib.import_module("app.workflow.adc_shared.repository")
Conflict: type[Exception] = _adc.Conflict
RUNS: str = _adc.RUNS
SAMPLES: str = _adc.SAMPLES
REVIEWS: str = _adc.REVIEWS

_RETRY_AFTER_SECONDS = 30.0
_PAGE = 500

_repo: Any = None
_next_attempt = 0.0
_lock = threading.Lock()


class StoreUnavailable(Exception):
    """Qdrant could not be reached or failed unexpectedly; the caller should carry on in memory."""


def build_repository(client: QdrantClient) -> Any:
    """A `Repository` over `client`; creates the three collections (and their payload indexes)."""

    return _adc.Repository(client)


def set_repository(repo: Any) -> None:
    """Installs (or, with None, clears) the process-wide repository - tests pass one built over
    `QdrantClient(":memory:")` so nothing touches a real Qdrant."""

    global _repo, _next_attempt
    with _lock:
        _repo = repo
        _next_attempt = 0.0


def _repository() -> Any:
    global _repo, _next_attempt
    with _lock:
        if _repo is not None:
            return _repo
        if time.monotonic() < _next_attempt:
            raise StoreUnavailable("qdrant unavailable (retrying shortly)")
        try:
            _repo = build_repository(QdrantClient(url=settings.qdrant_url, timeout=30))
        except Exception as exc:
            _next_attempt = time.monotonic() + _RETRY_AFTER_SECONDS
            logger.warning("run store: cannot reach qdrant at %s: %s", settings.qdrant_url, exc)
            raise StoreUnavailable(str(exc)) from exc
        return _repo


async def _call[T](fn: Callable[[Any], T]) -> T:
    def work() -> T:
        return fn(_repository())

    try:
        return await asyncio.to_thread(work)
    except (StoreUnavailable, Conflict, KeyError, ValueError):
        raise
    except Exception as exc:
        logger.warning("run store: qdrant call failed: %s", exc)
        raise StoreUnavailable(str(exc)) from exc


async def save_run(run_id: str, result: dict[str, Any]) -> None:
    """Persists the finished `WorkflowState` (as a dict): the run itself plus one point per prepared
    sample with its inference result. Idempotent for identical content."""

    await _call(lambda repo: repo.save_run(run_id, result))


async def get_sample(run_id: str, sample_id: str) -> dict[str, Any]:
    point: dict[str, Any] = await _call(lambda repo: repo.sample(run_id, sample_id))
    return point


def _page_of(
    fetch: Callable[[Any, str | None], dict[str, Any]], cursor: str | None, repo: Any
) -> dict[str, Any]:
    return fetch(repo, cursor)


async def _pages(fetch: Callable[[Any, str | None], dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = await _call(functools.partial(_page_of, fetch, cursor))
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            return items


def _samples_page(repo: Any, run_id: str, cursor: str | None) -> dict[str, Any]:
    repo.ready_run(run_id)
    conditions = [models.FieldCondition(key="run_id", match=models.MatchValue(value=run_id))]
    items, next_cursor = repo.page(SAMPLES, _PAGE, cursor, conditions)
    return {"items": items, "next_cursor": next_cursor}


async def sample_points(run_id: str) -> list[dict[str, Any]]:
    """Every sample point of a run (`sample` + `inference` + `final_decision`), reviewed or not.
    KeyError when the run is not stored."""

    return await _pages(lambda repo, cursor: _samples_page(repo, run_id, cursor))


async def review_cases(run_id: str) -> list[dict[str, Any]]:
    """Every REVIEW_REQUIRED sample point of a run (`sample` + `inference`)."""

    return await _pages(lambda repo, cursor: repo.review_cases(run_id, _PAGE, cursor))


async def save_review(run_id: str, sample_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Stores Agent 2's output. The first one is immutable: identical retries succeed, a different
    result raises `Conflict`."""

    record: dict[str, Any] = await _call(lambda repo: repo.save_review(run_id, sample_id, result))
    return record


async def save_decision(run_id: str, sample_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    """Adds the operator's decision to the sample's review point without touching Agent 2's
    evidence; a later decision replaces the earlier one."""

    record: dict[str, Any] = await _call(
        lambda repo: repo.save_decision(run_id, sample_id, decision)
    )
    return record


async def reviews_for_run(run_id: str) -> list[dict[str, Any]]:
    return await _pages(lambda repo, cursor: repo.reviews_for_run(run_id, _PAGE, cursor))
