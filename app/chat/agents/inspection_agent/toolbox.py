"""The ReAct agent's tools (react.py): the inspection steps themselves plus three read-only helpers,
each a LangChain `@tool` closing over one inspection's run. None takes arguments - the image and
context are the run's, so the model never supplies (or invents) them. The docstrings are what the
model reads as each tool's description.

`ToolBox` is what the tools call: it checks every step against policy.py before executing it, so an
out-of-order request is refused with the reason, and it serialises calls - some providers batch
several tool calls into one turn, and the steps all mutate the one shared run, so each must see the
previous one's result.
"""

import asyncio
import json
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.chat.agents.inspection_agent import policy, steps
from app.chat.agents.inspection_agent.image_quality import image_quality
from app.chat.agents.inspection_agent.state import InspectionRun, Stage
from app.shared import inference


class ToolBox:
    """Executes the model's tool calls against one run. Every method returns the JSON string the
    model reads back; none raises."""

    def __init__(self, run: InspectionRun) -> None:
        self.run = run
        self._lock = asyncio.Lock()

    async def call(self, name: str) -> str:
        async with self._lock:
            if name in policy.STEPS:
                return await self._step(name)
            if name == "get_scores":
                return self._get_scores()
            if name == "check_image_quality":
                return self._check_image_quality()
            if name == "list_models":
                return await self._list_models()
            return json.dumps({"error": f"unknown tool {name!r}"})

    async def _step(self, name: str) -> str:
        allowed, reason = policy.check(name, self.run)
        if not allowed:
            return json.dumps({"error": f"{name} is not allowed right now: {reason}"})

        seen = len(self.run.observations)
        await steps.execute(name, self.run)
        payload: dict[str, Any] = {"result": self.run.observations[seen:]}
        if self.run.error:
            payload["error"] = self.run.error
        return json.dumps(payload)

    def _get_scores(self) -> str:
        # Read-only: reports what the classifiers already stored, so there is no ordering to enforce.
        if self.run.region is None:
            return json.dumps({"error": "nothing classified yet; run classify_region first."})
        payload = {"region": _scores(self.run.region)}
        if self.run.defect is not None:
            payload["defect"] = _scores(self.run.defect)
        return json.dumps(payload)

    def _check_image_quality(self) -> str:
        quality = image_quality(self.run.request.image_bytes)
        if not quality.pop("readable"):
            return json.dumps({"error": "image could not be decoded."})
        return json.dumps(quality)

    async def _list_models(self) -> str:
        try:
            models = await inference.list_models()
        except (inference.InferenceNotConfigured, inference.InferenceError) as exc:
            return json.dumps({"error": f"could not list models: {exc}"})
        return json.dumps(
            {"models": [{"name": m.name, "version": m.version, "labels": m.labels} for m in models]}
        )


def _scores(stage: Stage) -> dict[str, Any]:
    return {
        "label": stage.label,
        "confidence": round(stage.confidence, 4),
        "scores": stage.ranked_scores(),
    }


def build_tools(toolbox: ToolBox) -> list[BaseTool]:
    @tool(policy.VERIFY_IMAGE)
    async def verify_image() -> str:
        """Check that the attached file decodes as an image."""
        return await toolbox.call(policy.VERIFY_IMAGE)

    @tool(policy.VALIDATE_MEASUREMENTS)
    async def validate_measurements() -> str:
        """Validate the measurements in the attached inspection XML. Only when an XML is attached."""
        return await toolbox.call(policy.VALIDATE_MEASUREMENTS)

    @tool(policy.CLASSIFY_REGION)
    async def classify_region() -> str:
        """Stage 1: classify which component region (Body, Lead or Text) the image shows."""
        return await toolbox.call(policy.CLASSIFY_REGION)

    @tool(policy.CLASSIFY_DEFECT)
    async def classify_defect() -> str:
        """Stage 2: classify the defect using the model for the region found in stage 1. Only after
        a confident classify_region."""
        return await toolbox.call(policy.CLASSIFY_DEFECT)

    @tool("get_scores")
    async def get_scores() -> str:
        """Read-only: the full per-label score distributions from the classifiers run so far,
        highest first - use it to report the runner-up labels."""
        return await toolbox.call("get_scores")

    @tool("check_image_quality")
    async def check_image_quality() -> str:
        """Read-only: size, brightness, contrast and blur score of the attached image. Never
        affects the verdict."""
        return await toolbox.call("check_image_quality")

    @tool("list_models")
    async def list_models() -> str:
        """List the classification models currently live and their versions."""
        return await toolbox.call("list_models")

    return [
        verify_image,
        validate_measurements,
        classify_region,
        classify_defect,
        get_scores,
        check_image_quality,
        list_models,
    ]
