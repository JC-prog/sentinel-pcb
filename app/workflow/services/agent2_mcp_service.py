"""Agent 2's explanation as a service other parts of the app can ask - over MCP, as its own process.

The teammate's FastMCP server (src/agent2_explainability/mcp/agent2_mcp_server.py) is kept exactly as
it was ported. It is not served directly, for three reasons found when this was built:

- it does not load under a real FastMCP (`case_context_retrieval_tool` takes `**kwargs`, which
  FastMCP rejects), so it is imported with a plain stand-in for the decorator and its tools used as
  the ordinary functions they are;
- `visual_evidence_tool` and `measurement_evidence_tool` read whatever path they are handed, and its
  own upstream security test fails for `../../etc/passwd` - so the raw tools are never reachable
  from the network;
- `measurement_evidence_tool` invents telemetry when it finds none (nominal values, even "FAIL"
  values keyed off the image's file name), and `visual_evidence_tool` returns a constant bounding
  box. The Work tab's Agent 2 was re-ported precisely to stop inventing telemetry, so those are
  dropped here: telemetry is used only when it was read from an inspection XML, otherwise it is
  "not recorded".

What is served is one tool, `explain_inspection`, that takes an *uploaded file's name* (never a
path), runs the ported tools in a fixed order and returns a grounded, advisory explanation.

Run it with `python -m app.workflow.services.agent2_mcp_service` (AGENT2_MCP_HOST / _PORT); the chat's
explain agent calls it at AGENT2_MCP_URL. It needs OPENAI_API_KEY in its environment, and the same
uploads directory the backend writes to.
"""

import contextlib
import importlib
import logging
import os
import sys
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.shared.config.settings import settings
from app.shared.modelops.run_drift import normalize_defect

logger = logging.getLogger("agent2-mcp-service")

_WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
_PORTED_SERVER = "app.workflow.src.agent2_explainability.mcp.agent2_mcp_server"

# The guardrail schema's classes -> the canonical names the rest of the app compares in
# (shared/modelops/run_drift.py). UNRESOLVED means Agent 2 declined to decide.
GUARDRAIL_TO_CANONICAL: dict[str, str | None] = {
    "MISSING": "missing part",
    "SHIFTED": "shifted",
    "WRONG_PART": "wrong part",
    "TOMBSTONE": "tombstone",
    "SOLDER_INSUFFICIENT": "solder insufficient",
    "PASS": "no defect",
    "UNRESOLVED": None,
}

# What the ported visual tool says when it could not look at the image.
_VISUAL_FAILED = (
    "Optical feature extraction timed out or failed",
    "Component site image not found",
)


@contextlib.contextmanager
def _plain_tool_decorator() -> Iterator[None]:
    """Makes `from fastmcp import FastMCP` give a stand-in whose `tool()` returns the function
    unchanged, for the length of one import. The real FastMCP would reject the ported module."""

    class _PlainFastMCP:
        def __init__(self, name: str = "") -> None:
            self.name = name

        def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Any]:
            return lambda fn: fn

    stub = types.ModuleType("fastmcp")
    stub.FastMCP = _PlainFastMCP  # type: ignore[attr-defined]
    saved = sys.modules.get("fastmcp")
    sys.modules["fastmcp"] = stub
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("fastmcp", None)
        else:
            sys.modules["fastmcp"] = saved


@dataclass(frozen=True)
class Tools:
    """The ported functions this service uses, so a test can stand in for them (they call OpenAI)."""

    precedents: Callable[..., dict[str, Any]]
    visual: Callable[..., dict[str, Any]]
    measurement: Callable[..., dict[str, Any]]
    grounding: Callable[..., dict[str, Any]]


def ensure_openai_key() -> None:
    """The ported tools read `OPENAI_API_KEY` straight from the process environment, but the app's
    settings also read it from `.env`, where a service started with `python -m ...` finds it without
    ever exporting it. Mirror it, as the Work tab's run does (services/streaming.py), so a key that
    works for the backend works here. An environment variable that is already set wins."""

    if not os.environ.get("OPENAI_API_KEY"):
        key = settings.orchestrator_api_key or settings.openai_api_key
        if key:
            os.environ["OPENAI_API_KEY"] = key


def load_ported_tools() -> Tools:
    ensure_openai_key()
    root = str(_WORKFLOW_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)  # the ported code uses bare `src.` / `models.` imports
    with _plain_tool_decorator():
        module = importlib.import_module(_PORTED_SERVER)
    return Tools(
        precedents=module.case_context_retrieval_tool,
        visual=module.visual_evidence_tool,
        measurement=module.measurement_evidence_tool,
        grounding=module.grounding_and_self_check_tool,
    )


def resolve_upload(name: str) -> Path | None:
    """A file in the uploads directory by its stored name, or None. A name is never a path: anything
    with a separator, or that resolves outside the directory, is refused."""

    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return None
    root = Path(settings.chat_upload_dir).resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, "advisory": True}


def _reasoning_prompt(
    *,
    board_id: str,
    component_ref: str,
    issue_symptom: str,
    model_label: str,
    model_confidence: float | None,
    precedents: dict[str, Any],
    visual_description: str,
    telemetry: dict[str, Any] | None,
) -> str:
    baseline = model_label or "no defect model answered"
    if model_confidence is not None and model_label:
        baseline += f" (confidence {model_confidence:.0%})"
    if telemetry:
        telemetry_text = ", ".join(
            f"{k}={v}"
            for k, v in telemetry.items()
            if k not in {"evidence_weight", "override_policy"} and v not in (None, "")
        )
    else:
        telemetry_text = "NOT RECORDED - no inspection XML reading was available. Do not assume any."
    return (
        f"Board: {board_id or 'unknown'}; component: {component_ref or 'unknown'}.\n"
        f"Reported symptom: {issue_symptom or 'none given'}.\n"
        f"The classifier's call, for context only - judge the image independently and do not defer "
        f"to it: {baseline}.\n\n"
        f"Tool 1 (IPC standard and precedents):\n{precedents.get('ipc_standard', '')}\n\n"
        f"Tool 2 (visual evidence):\n{visual_description}\n\n"
        f"Tool 3 (telemetry, secondary):\n{telemetry_text}\n"
    )


def explain(
    image_name: str,
    *,
    board_id: str = "",
    component_ref: str = "",
    issue_symptom: str = "",
    model_label: str = "",
    model_confidence: float | None = None,
    xml_name: str = "",
    tools: Tools | None = None,
) -> dict[str, Any]:
    """The grounded, advisory explanation of one uploaded image. Never raises for an expected
    failure: it returns `status: "unavailable"` with the reason, so a caller can say so plainly."""

    image = resolve_upload(image_name)
    if image is None:
        return _unavailable("the image is not in the uploads this service can read")
    used = tools or load_ported_tools()

    try:
        precedents = used.precedents(
            component_ref=component_ref, board_id=board_id, issue_symptom=issue_symptom
        )
        visual = used.visual(
            image_path=str(image),
            prompt=(
                "Describe this PCB component site: is the part present and aligned, and what is "
                "the solder fillet like? Report only what is visible."
            ),
        )
    except Exception:
        logger.exception("agent 2 evidence step failed")
        return _unavailable("an evidence step failed")

    description = str(visual.get("visual_description") or "")
    if not description or description.startswith(_VISUAL_FAILED):
        return _unavailable("the vision step could not look at the image")

    # Telemetry only when it was actually read from an inspection XML. The ported tool otherwise
    # returns invented nominal values, so it is not called at all without one.
    telemetry: dict[str, Any] | None = None
    xml = resolve_upload(xml_name) if xml_name else None
    if xml is not None:
        try:
            record = used.measurement(
                board_id=board_id, component_ref=component_ref, image_path="", xml_path=str(xml)
            )
        except Exception:
            logger.exception("agent 2 telemetry step failed")
            record = {}
        source = str(record.get("source") or "")
        telemetry = None if source.startswith("fallback") or not record else record

    prompt = _reasoning_prompt(
        board_id=board_id,
        component_ref=component_ref,
        issue_symptom=issue_symptom,
        model_label=model_label,
        model_confidence=model_confidence,
        precedents=precedents,
        visual_description=description,
        telemetry=telemetry,
    )
    try:
        grounded = used.grounding(reasoning_prompt=prompt)
    except Exception:
        logger.exception("agent 2 grounding step failed")
        return _unavailable("the reasoning step failed")

    return _result(
        grounded,
        model_label=model_label,
        visual=description,
        telemetry_recorded=telemetry is not None,
        had_precedents=bool(precedents.get("similar_cases")),
    )


def _result(
    grounded: dict[str, Any],
    *,
    model_label: str,
    visual: str,
    telemetry_recorded: bool,
    had_precedents: bool,
) -> dict[str, Any]:
    status = str(grounded.get("guardrail_status") or "FAILED")
    output = grounded.get("output") if isinstance(grounded.get("output"), dict) else None
    base: dict[str, Any] = {
        "advisory": True,
        "guardrail": {
            "status": status,
            "hallucination_flagged": bool(grounded.get("hallucination_flagged")),
            "note": grounded.get("error"),
        },
        "evidence": {
            "visual": visual,
            "telemetry_recorded": telemetry_recorded,
            "precedents_found": had_precedents,
        },
    }
    if output is None:
        return {**base, "status": "unavailable", "reason": "the explanation did not pass its checks"}

    canonical = GUARDRAIL_TO_CANONICAL.get(str(output.get("defect_classification")))
    model_canonical = normalize_defect(model_label) if model_label else None
    return {
        **base,
        "status": "flagged" if status == "FLAGGED" else "ok",
        "label": canonical,
        "confidence": output.get("confidence"),
        "summary": output.get("explanation_summary"),
        "visual_evidence_cited": output.get("visual_evidence_cited"),
        "ipc_precedent_cited": output.get("ipc_precedent_cited"),
        "model_label": model_canonical,
        "agrees_with_model": (canonical == model_canonical) if canonical and model_canonical else None,
    }


def build_server(tools: Tools | None = None) -> Any:
    """The FastMCP server with the one guarded tool. `tools` lets a test replace the ported
    functions (which call OpenAI)."""

    from fastmcp import FastMCP

    server = FastMCP("Agent2-Explainability")

    @server.tool
    def explain_inspection(
        image_name: str,
        board_id: str = "",
        component_ref: str = "",
        issue_symptom: str = "",
        model_label: str = "",
        model_confidence: float | None = None,
        xml_name: str = "",
    ) -> dict[str, Any]:
        """Explains one uploaded inspection image, grounded in what is visible and in IPC criteria.
        Advisory: it never decides a verdict or saves anything. `image_name` is the stored upload's
        file name, not a path."""

        return explain(
            image_name,
            board_id=board_id,
            component_ref=component_ref,
            issue_symptom=issue_symptom,
            model_label=model_label,
            model_confidence=model_confidence,
            xml_name=xml_name,
            tools=tools,
        )

    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = build_server()
    server.run(
        transport="http", host=settings.agent2_mcp_host, port=settings.agent2_mcp_port
    )


if __name__ == "__main__":
    main()
