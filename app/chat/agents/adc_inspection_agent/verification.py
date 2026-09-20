"""Ported from orchestrator-agent/adc_agentic_project's verification/{image_quality,image_alignment}.py,
adapted to operate on in-memory bytes rather than file paths (the case image is already in memory;
only the golden image needs a disk read, done by the caller via settings.case_golden_image_dir).

The source project uses OpenCV (cv2.phaseCorrelate, cv2.Laplacian) for both checks. This project
deliberately doesn't depend on opencv-python - it's a large dependency for two narrow numeric
checks, and this app's philosophy is to keep heavy vision/ML libraries out of the chat backend
(see inference/'s own separate pyproject.toml). numpy (already a dependency) is sufficient: a
manual Laplacian via shifted-array differencing for blur, and numpy.fft for phase correlation -
the same cross-power-spectrum algorithm cv2.phaseCorrelate implements.
"""

import io
from typing import Any

import numpy as np
from PIL import Image


def _to_grayscale_array(image_bytes: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(image_bytes)) as img:
        return np.asarray(img.convert("L"), dtype=np.float64)


def image_quality(image_bytes: bytes) -> dict[str, Any]:
    """Brightness (mean), contrast (std), and a blur score (variance of the Laplacian) - same
    metrics as the source project's image_quality.py. Only `readable` gates anything downstream;
    the rest are informational, same as the source."""

    try:
        gray = _to_grayscale_array(image_bytes)
    except Exception:  # noqa: BLE001 - any decode failure just means "not readable"
        return {"readable": False}

    # 4-neighbor Laplacian via np.roll - wraps at the edges rather than padding, a negligible
    # effect on the variance for a whole-image blur heuristic like this one.
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
        "brightness": float(gray.mean()),
        "contrast": float(gray.std()),
        "blur_score": float(laplacian.var()),
    }


def _zscore(arr: np.ndarray) -> np.ndarray:
    std = arr.std()
    if std == 0:
        return arr - arr.mean()
    return (arr - arr.mean()) / std


def estimate_translation(golden_bytes: bytes, defect_bytes: bytes) -> dict[str, Any]:
    """Sub-pixel translation between the golden and defect images via FFT-based phase correlation
    (the same cross-power-spectrum method cv2.phaseCorrelate implements): both images are cropped
    to a common size, z-score normalized, and the shift is read off the peak of the inverse FFT of
    their normalized cross-power spectrum. Returns dx/dy/shift_pixels/response, or an "error" key
    when either image can't be decoded or is too small to compare."""

    try:
        g1 = _to_grayscale_array(golden_bytes)
        g2 = _to_grayscale_array(defect_bytes)
    except Exception:  # noqa: BLE001 - degrade to an issue code rather than fail the pipeline
        return {"error": "UNREADABLE_IMAGE_PAIR"}

    height = min(g1.shape[0], g2.shape[0])
    width = min(g1.shape[1], g2.shape[1])
    if height < 8 or width < 8:
        return {"error": "IMAGE_TOO_SMALL_FOR_ALIGNMENT"}

    a = _zscore(g1[:height, :width])
    b = _zscore(g2[:height, :width])

    fa = np.fft.fft2(a)
    fb = np.fft.fft2(b)
    cross_power = fa * np.conj(fb)
    magnitude = np.abs(cross_power)
    magnitude[magnitude == 0] = 1e-12
    normalized = cross_power / magnitude
    correlation = np.fft.ifft2(normalized).real

    peak_y, peak_x = np.unravel_index(np.argmax(correlation), correlation.shape)
    response = float(correlation[peak_y, peak_x])

    dy = float(peak_y if peak_y <= height // 2 else peak_y - height)
    dx = float(peak_x if peak_x <= width // 2 else peak_x - width)
    shift_pixels = float(np.hypot(dx, dy))

    return {"dx": dx, "dy": dy, "shift_pixels": shift_pixels, "response": response}
