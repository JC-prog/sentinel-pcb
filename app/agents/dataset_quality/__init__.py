"""Dataset Quality (deterministic service): rule-based completeness/alignment/schema checks on
a `SamplePackage` before it's allowed to reach Multi-Modal Inference.

See docs/ADC-Design-Doc-Team10-V2.md §7. Pure rule evaluation - no model call, ever.
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from app.agents.dataset_preparation import SamplePackage

MIN_DIMENSION_PX = 64
MAX_IMAGE_BYTES = 25 * 1024 * 1024
SUPPORTED_FORMATS = {"PNG", "JPEG", "BMP", "TIFF"}


class QualityResult(BaseModel):
    passed: bool
    failed_checks: list[str]


def validate_sample(sample: SamplePackage) -> QualityResult:
    failed: list[str] = []

    if not sample.image:
        return QualityResult(passed=False, failed_checks=["image_missing"])

    if len(sample.image) > MAX_IMAGE_BYTES:
        failed.append("image_too_large")

    try:
        with Image.open(io.BytesIO(sample.image)) as img:
            img.verify()
        # Re-open after verify() - verify() leaves the file object unusable for further reads.
        with Image.open(io.BytesIO(sample.image)) as img:
            width, height = img.size
            image_format = (img.format or "").upper()
    except UnidentifiedImageError:
        return QualityResult(passed=False, failed_checks=["image_undecodable"])

    if width < MIN_DIMENSION_PX or height < MIN_DIMENSION_PX:
        failed.append("image_below_minimum_resolution")

    if image_format not in SUPPORTED_FORMATS:
        failed.append("unsupported_image_format")

    return QualityResult(passed=len(failed) == 0, failed_checks=failed)
