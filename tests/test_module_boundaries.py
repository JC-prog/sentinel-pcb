"""Enforces app/'s module boundaries: `shared` is the only code chat and workflow may have in
common, and neither feature module may import the other. Scans every import statement (including
lazy, in-function ones) with `ast`, so a stray `from app.chat...` deep inside a workflow helper
fails here instead of quietly re-coupling the two modules.
"""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# module -> top-level app packages it must NOT import from
FORBIDDEN: dict[str, set[str]] = {
    "shared": {"chat", "workflow"},
    "chat": {"workflow"},
    "workflow": {"chat"},
}


def _imported_app_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return [(line, mod) for line, mod in found if mod == "app" or mod.startswith("app.")]


@pytest.mark.parametrize("module", sorted(FORBIDDEN))
def test_module_does_not_import_forbidden_modules(module: str) -> None:
    violations = []
    for path in sorted((APP_DIR / module).rglob("*.py")):
        for line, imported in _imported_app_modules(path):
            top = imported.split(".")[1] if "." in imported else ""
            if top in FORBIDDEN[module]:
                violations.append(f"{path.relative_to(APP_DIR.parent)}:{line} imports {imported}")
    assert not violations, "module boundary violated:\n" + "\n".join(violations)


def test_app_only_contains_known_modules() -> None:
    """A new top-level package under app/ needs a deliberate decision about which side of the
    boundary it's on - add it to FORBIDDEN (or here) rather than letting it be unchecked."""

    packages = {p.name for p in APP_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert packages == set(FORBIDDEN)
