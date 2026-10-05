"""Tool-selection eval harness for the chat supervisor (app/chat/agents/supervisor.py).

It runs the *real* supervisor against a *real* LLM, but every tool is swapped for a stub that only
records the call and returns canned JSON, so a case measures the model's decisions - which tool, with
which arguments - and nothing downstream (no DB, no inference service). Scoring is exact match on the
recorded calls; there is no LLM judge here.

A case (cases.jsonl, one JSON object per line):
    id, message                  required
    role                         "qa" (default) | "admin" - decides which tools are even offered
    image                        an image is attached (offers inspect_image)
    history                      prior [{"role": "user"|"assistant", "content": ...}] turns
    expect_tools                 tools that must be called (any order)
    may_also_call                extra tools tolerated; any other call fails the case
    forbid_tools                 tools that must not be called (reported distinctly)
    expect_args                  {tool: {arg: value}} - each given arg must match (case-insensitive)
    safety                       true -> must pass on every repeat, not just most of them
    tags                         free-form, for filtering/reporting
"""

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from app.chat.agents import tool_registry
from app.chat.agents.supervisor import Answer, ToolStarted, run_turn
from app.chat.agents.toolkit import ChatTool
from app.chat.core.chat import ChatTurn
from app.chat.core.tools import ToolContext
from app.chat.services.messages import CHAT_SYSTEM_PROMPT, build_messages
from app.shared.db import UserRole

CASES_PATH = Path(__file__).parent / "cases.jsonl"

# What a stubbed tool answers with. Only needs to be plausible enough for the model to write a reply.
CANNED_RESULTS: dict[str, dict[str, Any]] = {
    "inspect_image": {"case_id": "CASE-000099", "verdict": "REVIEW_REQUIRED", "defect_label": "open"},
    "relabel_case": {"status": "proposed", "case_id": "CASE-000012", "proposed_label": "short"},
    "confirm_relabel": {"status": "confirmed", "case_id": "CASE-000012"},
    "get_drift_summary": {"status": "ok", "review_rate": 0.12, "correction_rate": 0.03},
    "report_model_drift": {"status": "recorded", "report_id": "r1"},
    "draft_retraining_plan": {"status": "pending_approval", "job_id": "j1"},
    "monitoring_status": {"status": "ok", "agents": "enabled"},
}


@dataclass(frozen=True)
class Case:
    id: str
    message: str
    role: str = "qa"
    image: bool = False
    history: tuple[tuple[str, str], ...] = ()
    expect_tools: frozenset[str] = frozenset()
    may_also_call: frozenset[str] = frozenset()
    forbid_tools: frozenset[str] = frozenset()
    expect_args: dict[str, dict[str, Any]] = field(default_factory=dict)
    safety: bool = False
    tags: tuple[str, ...] = ()


@dataclass
class Trace:
    called: list[str] = field(default_factory=list)
    args: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    answer: str = ""


@dataclass(frozen=True)
class Verdict:
    passed: bool
    problems: tuple[str, ...]


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(
            Case(
                id=raw["id"],
                message=raw["message"],
                role=raw.get("role", "qa"),
                image=raw.get("image", False),
                history=tuple((h["role"], h["content"]) for h in raw.get("history", [])),
                expect_tools=frozenset(raw.get("expect_tools", [])),
                may_also_call=frozenset(raw.get("may_also_call", [])),
                forbid_tools=frozenset(raw.get("forbid_tools", [])),
                expect_args=raw.get("expect_args", {}),
                safety=raw.get("safety", False),
                tags=tuple(raw.get("tags", [])),
            )
        )
    return cases


def _stub(chat_tool: ChatTool, trace: Trace) -> ChatTool:
    """The same tool - name, description, argument schema - with its body replaced."""

    async def record(**kwargs: Any) -> str:
        kwargs.pop("runtime", None)  # injected by LangGraph, never the model's
        trace.args.setdefault(chat_tool.name, []).append(kwargs)
        return json.dumps(CANNED_RESULTS.get(chat_tool.name, {"status": "ok"}))

    # args_schema is swapped for the model-facing schema (no injected `runtime`): the shared
    # original is never touched, and the stub needs no ToolContext.
    stubbed = chat_tool.tool.model_copy(
        update={"coroutine": record, "args_schema": chat_tool.tool.tool_call_schema}
    )
    return dataclasses.replace(chat_tool, tool=stubbed)


def offered_tools(case: Case) -> list[ChatTool]:
    return tool_registry.available(role=UserRole[case.role.upper()], has_image=case.image)


async def run_case(case: Case, *, provider: str) -> Trace:
    trace = Trace()
    tools = [_stub(t, trace) for t in offered_tools(case)]
    turns = [
        ChatTurn.model_validate({"role": role, "content": content}) for role, content in case.history
    ]
    messages = build_messages(
        CHAT_SYSTEM_PROMPT,
        turns,
        case.message,
        image_ids=["eval-image"] if case.image else None,
    )
    # The stubs never touch the session, and the user is only read for its id (Langfuse metadata).
    ctx = cast(
        ToolContext,
        SimpleNamespace(
            session=None,
            user=SimpleNamespace(id="eval-user", role=case.role),
            conversation_id="eval-conversation",
            turn_started_at=datetime.now(UTC),
            image_ids=("eval-image",) if case.image else (),
            xml_ids=(),
        ),
    )
    async for event in run_turn(provider=provider, messages=messages, ctx=ctx, tools=tools):
        if isinstance(event, ToolStarted):
            trace.called.append(event.name)
        elif isinstance(event, Answer):
            trace.answer = event.text
    return trace


def score(case: Case, trace: Trace) -> Verdict:
    called = set(trace.called)
    problems: list[str] = []

    if missing := case.expect_tools - called:
        problems.append(f"missing tool call(s): {sorted(missing)}")
    if forbidden := case.forbid_tools & called:
        problems.append(f"called forbidden tool(s): {sorted(forbidden)}")
    if unexpected := called - case.expect_tools - case.may_also_call - case.forbid_tools:
        problems.append(f"unexpected tool call(s): {sorted(unexpected)}")

    for tool_name, wanted in case.expect_args.items():
        calls = trace.args.get(tool_name, [])
        for arg, value in wanted.items():
            if not any(str(c.get(arg, "")).strip().lower() == str(value).lower() for c in calls):
                got = [c.get(arg) for c in calls]
                problems.append(f"{tool_name}.{arg}: expected {value!r}, got {got}")

    return Verdict(passed=not problems, problems=tuple(problems))
