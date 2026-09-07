import numpy as np
from PIL import Image

from inference_service.manifest import ModelSpec
from inference_service.preprocess import preprocess


def _spec(**overrides: object) -> ModelSpec:
    base: dict[str, object] = {
        "name": "m",
        "repo_id": "r",
        "labels": ["a", "b"],
        "input_size": (8, 10),  # H, W
        "mean": (0.0, 0.0, 0.0),
        "std": (1.0, 1.0, 1.0),
    }
    base.update(overrides)
    return ModelSpec(**base)  # type: ignore[arg-type]


def test_nchw_shape_and_dtype() -> None:
    out = preprocess(Image.new("RGB", (4, 4), (255, 0, 0)), _spec())
    assert out.shape == (1, 3, 8, 10)
    assert out.dtype == np.float32


def test_nhwc_layout() -> None:
    out = preprocess(Image.new("RGB", (4, 4), (255, 0, 0)), _spec(layout="NHWC"))
    assert out.shape == (1, 8, 10, 3)


def test_pixels_scaled_to_unit_range_before_normalization() -> None:
    out = preprocess(Image.new("RGB", (4, 4), (255, 255, 255)), _spec())
    assert np.allclose(out, 1.0)
    out_black = preprocess(Image.new("RGB", (4, 4), (0, 0, 0)), _spec())
    assert np.allclose(out_black, 0.0)


def test_normalization_is_applied() -> None:
    spec = _spec(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))
    out = preprocess(Image.new("RGB", (4, 4), (255, 255, 255)), spec)
    assert np.allclose(out, 1.0)  # (1.0 - 0.5) / 0.5


def test_bgr_swaps_channels() -> None:
    spec = _spec(color="BGR")
    out = preprocess(Image.new("RGB", (4, 4), (255, 0, 0)), spec)  # pure red
    # channel 0 is now blue (0), channel 2 is red (1)
    assert np.allclose(out[0, 0], 0.0)
    assert np.allclose(out[0, 2], 1.0)
