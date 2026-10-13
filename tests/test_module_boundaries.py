"""Enforces app/'s module boundaries: `shared` is the only code the feature modules (chat,
workflow, modelops) may have in common, and none of them may import another. Scans every import statement (including
lazy, in-function ones) with `ast`, so a stray `from app.chat...` deep inside a workflow helper
fails here instead of quietly re-coupling the two modules.
"""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# module -> top-level app packages it must NOT import from
FORBIDDEN: dict[str, set[str]] = {
    "shared": {"chat", "workflow", "modelops"},
    "chat": {"workflow", "modelops"},
    "workflow": {"chat", "modelops"},
    "modelops": {"chat", "workflow"},
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

    # Only directories that actually hold Python source count: a folder containing nothing but
    # stale __pycache__ (left behind, untracked, when a package is moved or renamed) isn't a package.
    packages = {
        p.name for p in APP_DIR.iterdir() if p.is_dir() and any(p.rglob("*.py"))
    }
    assert packages == set(FORBIDDEN)
