"""Image quality metrics for an inspection image - readability, size, brightness, contrast and a
blur score. Only `readable` gates anything (the verifier refuses an unreadable image); the rest
are informational, for the model to mention and the user to see.

Adapted from orchestrator-agent/adc_agentic_project's verification/image_quality.py to work on
in-memory bytes, with numpy (already a dependency) standing in for OpenCV: a 4-neighbour Laplacian
via shifted arrays for the blur score.
"""

import io
from typing import Any

import numpy as np
from PIL import Image


def image_quality(image_bytes: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            gray = np.asarray(img.convert("L"), dtype=np.float64)
    except Exception:  # noqa: BLE001 - any decode failure just means "not readable"
        return {"readable": False}

    # np.roll wraps at the edges rather than padding - a negligible effect on the variance for a
    # whole-image blur heuristic like this one.
    laplacian = (
        -4.0 * gray
        + np.roll(gray, 1, axis=0)
        + np.roll(gray, -1, axis=0)
        + np.roll(gray, 1, axis=1)
        + np.roll(gray, -1, axis=1)
    )
    return {
        "readable": True,
        "width": int(gray.shape[1]),
        "height": int(gray.shape[0]),
        "brightness": round(float(gray.mean()), 2),
        "contrast": round(float(gray.std()), 2),
        "blur_score": round(float(laplacian.var()), 2),
    }
