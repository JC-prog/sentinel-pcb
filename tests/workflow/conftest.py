"""Workflow tests never talk to a real Qdrant: the run store is pointed at an in-memory one (the
payload-only collections need no server), so a dev Qdrant on :6333 is neither read nor written."""

import warnings
from collections.abc import Iterator
from typing import Any

import pytest
from qdrant_client import QdrantClient

from app.workflow.services import run_store


@pytest.fixture(autouse=True)
def run_store_repo() -> Iterator[Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "payload indexes have no effect in the local Qdrant"
        repo = run_store.build_repository(QdrantClient(":memory:"))
    run_store.set_repository(repo)
    yield repo
    run_store.set_repository(None)
