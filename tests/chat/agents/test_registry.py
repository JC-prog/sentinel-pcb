"""The tool registry and the toolkit every chat tool is built from. Uses stand-in `@tool`s, so it
tests the rules, not any agent."""

import json
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from langchain_core.tools import tool

from app.chat.agents import tool_registry
from app.chat.agents.access import TOOL_ROLES
from app.chat.agents.registry import ToolNotFound, ToolRegistry
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, required, returns_json
from app.shared.db.models import UserRole


def _stand_in(name: str, **options: Any) -> ChatTool:
    """A ChatTool named after a real role-table entry, so access.py applies to it."""

    @tool(name)
    @returns_json
    async def stand_in(runtime: Runtime, value: Annotated[str, "anything"] = "") -> dict[str, Any]:
        """A stand-in tool."""
        return {"value": value}

    return ChatTool(stand_in, label=name.title(), **options)


def test_get_raises_for_an_unknown_tool() -> None:
    with pytest.raises(ToolNotFound):
        ToolRegistry().get("does_not_exist")


def test_available_filters_by_role_switch_and_attached_image() -> None:
    registry = ToolRegistry(
        [
            _stand_in("get_drift_summary"),
            _stand_in("monitoring_status"),  # Admin only
            _stand_in("inspect_image", requires_image=True),
            _stand_in("relabel_case", enabled=lambda: False),  # feature switched off
        ]
    )

    def names(role: UserRole, has_image: bool) -> set[str]:
        return {t.name for t in registry.available(role=role, has_image=has_image)}

    assert names(UserRole.QA, False) == {"get_drift_summary"}
    assert names(UserRole.QA, True) == {"get_drift_summary", "inspect_image"}
    assert names(UserRole.ADMIN, False) == {"get_drift_summary", "monitoring_status"}


async def _run(fn: Any, **arguments: Any) -> Any:
    return json.loads(await fn(runtime=SimpleNamespace(context=None), **arguments))


async def test_returns_json_encodes_results_refusals_and_bugs() -> None:
    @returns_json
    async def ok(runtime: Runtime) -> dict[str, Any]:
        return {"fine": True}

    @returns_json
    async def refused(runtime: Runtime) -> dict[str, Any]:
        raise ToolRefused("not a label", valid_labels=["Golden"])

    @returns_json
    async def buggy(runtime: Runtime) -> dict[str, Any]:
        raise RuntimeError("password=hunter2")

    assert await _run(ok) == {"fine": True}
    assert await _run(refused) == {"error": "not a label", "valid_labels": ["Golden"]}
    bug = await _run(buggy)
    assert "failed unexpectedly" in bug["error"]
    assert "hunter2" not in bug["error"]  # a bug is logged, never shown to the model


def test_required_rejects_blank_values() -> None:
    assert required("  x ", "reason") == "x"
    for blank in (None, "", "   "):
        with pytest.raises(ToolRefused, match="reason is required"):
            required(blank, "reason")


def test_the_runtime_is_injected_and_never_part_of_what_the_model_sees() -> None:
    for chat_tool in tool_registry:
        schema = chat_tool.model_schema()
        assert "runtime" not in schema.get("properties", {}), chat_tool.name
        assert chat_tool.tool.description, chat_tool.name


def test_every_registered_tool_has_a_role_entry_and_a_label() -> None:
    """A tool missing from access.py would be invisible to everyone - fail loudly instead."""

    for chat_tool in tool_registry:
        assert chat_tool.name in TOOL_ROLES, chat_tool.name
        assert chat_tool.label and chat_tool.label != chat_tool.name, chat_tool.name
    assert set(TOOL_ROLES) == {t.name for t in tool_registry}
