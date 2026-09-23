"""Enforces that the chat agents under app/chat/agents/ are mutually independent: no agent package
may import another agent package (lazy, in-function imports included). What two agents both need -
case lookup, XML parsing, uploads - belongs in app/chat/services/ (or core/), not in whichever
agent happened to write it first. Same AST-scan approach as tests/test_module_boundaries.py.

The shared plumbing that sits *beside* the agents (registry.py, access.py, and the package
__init__ that registers every agent's tools) is allowed to see them all.
"""

import ast
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "chat" / "agents"
AGENTS_PREFIX = "app.chat.agents."


def _agent_packages() -> list[str]:
    return sorted(
        p.name for p in AGENTS_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"
    )


def _imported_agents(path: Path) -> list[tuple[int, str]]:
    """(line, agent package name) for every import of app.chat.agents.<agent>[...] in `path`."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # `from app.chat.agents import time_agent` names the agent in the alias, not the module
            if node.module == "app.chat.agents":
                modules = [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                modules = [node.module]
        else:
            continue
        for module in modules:
            if module.startswith(AGENTS_PREFIX):
                found.append((node.lineno, module.removeprefix(AGENTS_PREFIX).split(".")[0]))
    return found


def test_there_are_agents_to_check() -> None:
    assert {"inspection_agent", "case_agent", "monitoring_agent"} <= set(_agent_packages())


@pytest.mark.parametrize("agent", _agent_packages())
def test_agent_does_not_import_other_agents(agent: str) -> None:
    violations = []
    for path in sorted((AGENTS_DIR / agent).rglob("*.py")):
        for line, imported in _imported_agents(path):
            if imported != agent:
                violations.append(
                    f"{path.relative_to(AGENTS_DIR.parent.parent.parent)}:{line} "
                    f"imports agent {imported!r}"
                )
    assert not violations, (
        "agents must be independent - move shared code to app/chat/services/:\n"
        + "\n".join(violations)
    )
