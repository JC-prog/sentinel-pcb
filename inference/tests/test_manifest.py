from pathlib import Path

import pytest

from inference_service.manifest import Manifest, load_manifest

VALID = """
[[models]]
name = "a"
repo_id = "JcProg/a"
labels = ["x", "y"]

[[models]]
name = "b"
repo_id = "JcProg/b"
labels = ["p", "q"]
layout = "NHWC"
apply_softmax = false
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "models.toml"
    path.write_text(text)
    return path


def test_load_manifest_parses_entries(tmp_path: Path) -> None:
    manifest = _load(tmp_path, VALID)
    assert manifest.names() == ["a", "b"]
    assert manifest.models[1].layout == "NHWC"
    assert manifest.models[1].apply_softmax is False


def test_placeholder_repo_is_flagged(tmp_path: Path) -> None:
    manifest = _load(
        tmp_path,
        '[[models]]\nname = "a"\nrepo_id = "JcProg/REPLACE_ME"\nlabels = ["x"]\n',
    )
    assert manifest.models[0].is_placeholder is True


def test_duplicate_names_rejected(tmp_path: Path) -> None:
    text = (
        '[[models]]\nname = "dup"\nrepo_id = "r"\nlabels = ["x"]\n'
        '[[models]]\nname = "dup"\nrepo_id = "r2"\nlabels = ["y"]\n'
    )
    with pytest.raises(ValueError, match="duplicate model name"):
        _load(tmp_path, text)


def test_empty_manifest_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _load(tmp_path, "# nothing here\n")


def test_bad_layout_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _load(tmp_path, '[[models]]\nname = "a"\nrepo_id = "r"\nlabels = ["x"]\nlayout = "XYZ"\n')


def _load(tmp_path: Path, text: str) -> Manifest:
    return load_manifest(_write(tmp_path, text))
