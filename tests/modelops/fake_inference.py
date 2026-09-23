"""A stateful stand-in for the inference service, served through httpx's MockTransport, so the
Models tab's API is tested against realistic request/response behaviour - versions that swap and
roll back, jobs that progress or get forgotten - rather than canned one-liners."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.shared.config.settings import settings

_RealAsyncClient = httpx.AsyncClient
_LOADED_AT = "2026-09-20T10:00:00Z"


@dataclass
class _Model:
    version: str
    previous: str | None = None
    labels: list[str] = field(default_factory=lambda: ["Golden", "MissingPart"])


class FakeInference:
    def __init__(self) -> None:
        self.models: dict[str, _Model] = {"pcb_body_defect": _Model("JcProg/body@v1")}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.down = False  # every request fails to connect
        self.activate_error: tuple[int, str] | None = None  # (status, detail) to answer with
        self.calls: list[tuple[str, str]] = []

    # ------------------------------------------------------------------------ test controls

    def set_job(self, remote_id: str, **changes: Any) -> None:
        self.jobs[remote_id].update(changes)

    def only_job_id(self) -> str:
        (remote_id,) = self.jobs
        return remote_id

    def forget_jobs(self) -> None:
        """What a restart of the real service does to its in-memory jobs."""

        self.jobs.clear()

    # --------------------------------------------------------------------------- the service

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused")
        path = request.url.path
        self.calls.append((request.method, path))
        body = json.loads(request.content) if request.content else {}

        if request.method == "GET" and path == "/models":
            return httpx.Response(200, json=[self._info(name) for name in self.models])

        if request.method == "POST" and path.startswith("/models/"):
            _, _, name, action = path.split("/")
            if name not in self.models:
                return httpx.Response(404, text=f"unknown model {name!r}")
            model = self.models[name]
            if action == "activate":
                if self.activate_error:
                    status, detail = self.activate_error
                    return httpx.Response(status, text=detail)
                model.previous, model.version = (
                    model.version,
                    f"{body['repo_id']}@{body['revision']}",
                )
            elif action == "rollback":
                if model.previous is None:
                    return httpx.Response(409, text=f"{name!r} has no previous version")
                model.previous, model.version = model.version, model.previous
            return httpx.Response(200, json=self._info(name))

        if request.method == "POST" and path == "/jobs":
            for job in self.jobs.values():
                if job["client_ref"] == body["client_ref"]:
                    return httpx.Response(202, json=job)
            if body["model"] not in self.models:
                return httpx.Response(404, text="unknown model")
            remote_id = uuid.uuid4().hex
            self.jobs[remote_id] = {
                "id": remote_id,
                "client_ref": body["client_ref"],
                "model": body["model"],
                "base_version": body["base_version"] or self.models[body["model"]].version,
                "status": "queued",
                "progress": 0.0,
                "sample_count": len(body["samples"]),
                "created_at": datetime.now(UTC).isoformat(),
                "started_at": None,
                "finished_at": None,
                "error": None,
                "result": None,
            }
            return httpx.Response(202, json=self.jobs[remote_id])

        if path.startswith("/jobs/"):
            parts = path.split("/")
            found = self.jobs.get(parts[2])
            if found is None:
                return httpx.Response(404, text="unknown job")
            if request.method == "POST" and parts[3:] == ["cancel"]:
                if found["status"] in ("succeeded", "failed"):
                    return httpx.Response(409, text=f"job already {found['status']}")
                found["status"] = "cancelled"
            return httpx.Response(200, json=found)

        return httpx.Response(404, text="no such route in the fake")

    def _info(self, name: str) -> dict[str, Any]:
        model = self.models[name]
        return {
            "name": name,
            "version": model.version,
            "previous_version": model.previous,
            "loaded_at": _LOADED_AT,
            "labels": model.labels,
            "input_size": [640, 640],
        }


def succeeded(fake: FakeInference, remote_id: str, *, repo_id: str, revision: str) -> None:
    fake.set_job(
        remote_id,
        status="succeeded",
        progress=1.0,
        started_at=datetime.now(UTC).isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        result={
            "simulated": False,
            "artifact": {"repo_id": repo_id, "revision": revision},
            "metrics": {},
        },
    )


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeInference) -> None:
    """Route every httpx.AsyncClient the app creates to `fake`, and point the app at it."""

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return _RealAsyncClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001")
    monkeypatch.setattr(settings, "modelops_enabled", True)
