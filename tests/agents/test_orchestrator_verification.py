from io import BytesIO

from PIL import Image

from app.agents.adc_inspection_agent.verification import estimate_translation, image_quality


def _png_bytes(size: tuple[int, int] = (64, 64), color: tuple[int, int, int] = (120, 130, 140)) -> bytes:
    img = Image.new("RGB", size)
    pixels = img.load()
    assert pixels is not None
    # A gradient, not a flat fill - a perfectly flat image has zero-variance FFT phase content,
    # which makes phase correlation degenerate. A gradient gives estimate_translation something
    # real to lock onto.
    for x in range(size[0]):
        for y in range(size[1]):
            pixels[x, y] = (
                (color[0] + x) % 256,
                (color[1] + y) % 256,
                color[2],
            )
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_quality_reports_unreadable_for_garbage_bytes() -> None:
    result = image_quality(b"not-an-image")
    assert result == {"readable": False}


def test_image_quality_reports_dimensions_for_a_real_image() -> None:
    result = image_quality(_png_bytes((32, 16)))
    assert result["readable"] is True
    assert result["width"] == 32
    assert result["height"] == 16
    assert isinstance(result["brightness"], float)
    assert isinstance(result["blur_score"], float)


def test_estimate_translation_reports_error_for_unreadable_bytes() -> None:
    result = estimate_translation(b"not-an-image", _png_bytes())
    assert result == {"error": "UNREADABLE_IMAGE_PAIR"}


def test_estimate_translation_reports_near_zero_shift_for_identical_images() -> None:
    image_bytes = _png_bytes()
    result = estimate_translation(image_bytes, image_bytes)
    assert result["shift_pixels"] < 1.0


def test_estimate_translation_reports_error_for_too_small_images() -> None:
    result = estimate_translation(_png_bytes((4, 4)), _png_bytes((4, 4)))
    assert result == {"error": "IMAGE_TOO_SMALL_FOR_ALIGNMENT"}
