"""The inspect agent's image quality metrics: only `readable` gates anything; the rest is reported."""

from app.chat.agents.inspection_agent.image_quality import image_quality
from tests.chat.agents._helpers import png_bytes


def test_garbage_bytes_are_unreadable() -> None:
    assert image_quality(b"not-an-image") == {"readable": False}


def test_a_real_image_reports_its_size_and_rounded_metrics() -> None:
    result = image_quality(png_bytes((32, 16)))

    assert result["readable"] is True
    assert (result["width"], result["height"]) == (32, 16)
    for metric in ("brightness", "contrast", "blur_score"):
        assert isinstance(result[metric], float)
        assert result[metric] == round(result[metric], 2)
