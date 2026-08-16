import io

from PIL import Image

from app.agents.dataset_preparation import prepare_sample
from app.agents.dataset_quality import validate_sample


def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_valid_image_passes() -> None:
    sample = prepare_sample("wf-1", _png_bytes(256, 256), "board.png")
    result = validate_sample(sample)
    assert result.passed
    assert result.failed_checks == []


def test_undersized_image_fails_resolution_check() -> None:
    sample = prepare_sample("wf-1", _png_bytes(10, 10), "board.png")
    result = validate_sample(sample)
    assert not result.passed
    assert "image_below_minimum_resolution" in result.failed_checks


def test_undecodable_bytes_fail() -> None:
    sample = prepare_sample("wf-1", b"not an image", "board.png")
    result = validate_sample(sample)
    assert not result.passed
    assert result.failed_checks == ["image_undecodable"]


def test_missing_image_fails() -> None:
    sample = prepare_sample("wf-1", b"", "board.png")
    result = validate_sample(sample)
    assert not result.passed
    assert result.failed_checks == ["image_missing"]
