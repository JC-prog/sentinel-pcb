"""Dataset Preparation (deterministic service): assembles a `SamplePackage` from the raw
inspection event. See docs/ADC-Design-Doc-Team10-V2.md §7.

TODO(spec): golden/defect reference pairing is not implemented - there is no reference catalog
in this build pass. `SamplePackage.reference_image` is always `None` for now; wire it up once a
catalog exists, per the design doc's "no-match fallback" open question (§9).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SamplePackage(BaseModel):
    workflow_id: str
    image: bytes
    image_filename: str
    reference_image: bytes | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


def prepare_sample(
    workflow_id: str,
    image: bytes,
    image_filename: str,
    metadata: dict[str, str] | None = None,
) -> SamplePackage:
    """Deterministically assemble a `SamplePackage`. No LLM reasoning here by design - see
    ADC-Project-Proposal-Team10-Updated-v2.md, which explicitly treats reference pairing as
    non-agentic."""

    return SamplePackage(
        workflow_id=workflow_id,
        image=image,
        image_filename=image_filename,
        metadata=metadata or {},
    )
