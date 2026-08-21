"""Seeds the synthetic remediation-guidance corpus that Case Context retrieves from
(app.agents.explainability_review.case_context). Hand-authored for this project - not derived
from any real manufacturer documentation - see docs/AGENT-DESIGN.md.

Usage:
    uv run python scripts/seed_remediation_docs.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Repo root isn't on sys.path when this script is run directly (uv run python scripts/...) -
# unlike simulation/simulate_line.py, this script needs to import `app`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.base import Base
from app.db.models import RemediationDoc
from app.db.session import async_session_factory, engine
from app.services.embeddings import get_embedding_provider

# (defect_label, title, content) - 4 per label, covering: immediate handling, two root-cause
# angles, and a severity/escalation or rework note. Reuses the same illustrative labels as
# app.agents.multi_modal_inference.defect_classifier and planning/docs/SCENARIOS.md.
_DOCS: list[tuple[str, str, str]] = [
    (
        "Tombstone",
        "Tombstone Defect: Immediate Handling",
        (
            "A tombstoned component has lifted on one end during reflow, standing on its remaining "
            "solder joint like a headstone. Quarantine the board immediately - do not attempt manual "
            "reflow or press the component back down, as this can crack the component body or "
            "damage the remaining pad."
        ),
    ),
    (
        "Tombstone",
        "Tombstone Root Cause: Reflow Profile",
        (
            "The most common cause is an uneven reflow ramp rate combined with unequal thermal mass "
            "between the two pads a small component sits on - one side reflows and wets before the "
            "other, and surface tension pulls the component upright. Review the oven's zone "
            "temperatures and belt speed against the paste manufacturer's recommended profile."
        ),
    ),
    (
        "Tombstone",
        "Tombstone Root Cause: Pad Design",
        (
            "Asymmetric pad sizes or unequal copper pour/thermal relief under the two pads of a small "
            "passive (0402/0201) can cause one side to reach reflow temperature before the other. "
            "If tombstoning recurs on the same component/footprint across multiple boards, flag the "
            "pad design for review rather than treating each occurrence as an isolated process issue."
        ),
    ),
    (
        "Tombstone",
        "Tombstone Severity & Escalation Criteria",
        (
            "A single, isolated tombstone on a low-criticality passive is typically a rework item. "
            "Recurring tombstoning on the same board position/component across a production run "
            "indicates a systemic reflow or pad-design issue and should escalate to process "
            "engineering rather than being repeatedly reworked without investigation."
        ),
    ),
    (
        "Solder Bridge",
        "Solder Bridge: Immediate Handling",
        (
            "A solder bridge is excess solder forming an unintended electrical connection between "
            "adjacent pads or leads. Quarantine the board and do not power it on - a bridge can short "
            "adjacent nets and damage components downstream if energized."
        ),
    ),
    (
        "Solder Bridge",
        "Solder Bridge Root Cause: Stencil Aperture",
        (
            "An oversized or misaligned stencil aperture deposits more paste than the pad needs, "
            "which can wick sideways into a bridge during reflow, especially on fine-pitch parts. "
            "Check stencil aperture dimensions against IPC stencil design guidelines and inspect for "
            "stencil wear or misregistration on the printer."
        ),
    ),
    (
        "Solder Bridge",
        "Solder Bridge Root Cause: Reflow Temperature",
        (
            "Excessive peak reflow temperature or too long a time-above-liquidus can cause solder to "
            "become overly fluid and wick between closely spaced leads. This is more common on "
            "fine-pitch ICs than on passives - if bridging clusters on one package type, check that "
            "package's specific thermal profile rather than the oven's overall setpoint alone."
        ),
    ),
    (
        "Solder Bridge",
        "Solder Bridge Rework Guidance",
        (
            "Bridging rework is typically done with solder wick (desoldering braid) and a "
            "temperature-controlled iron, or localized hot air, by a trained technician only - "
            "aggressive manual rework risks lifting the pad or damaging adjacent fine-pitch leads. "
            "Re-inspect under magnification after rework, not just visually."
        ),
    ),
    (
        "Foreign Material",
        "Foreign Material: Immediate Handling",
        (
            "Suspected foreign material (debris, a stray component, contamination) near a component "
            "or pad should be photographed and the board quarantined - do not blow or brush at the "
            "debris, which can embed it further into a joint or spread it to adjacent pads."
        ),
    ),
    (
        "Foreign Material",
        "Foreign Material Root Cause: Flux Residue",
        (
            "Uncleaned flux residue is one of the most common causes of a foreign-material flag on "
            "AOI, and is frequently a false positive rather than a genuine contamination defect. "
            "Check the cleaning/no-clean flux process step and compare against known residue "
            "appearance for the flux type in use before treating it as a true contamination event."
        ),
    ),
    (
        "Foreign Material",
        "Foreign Material Root Cause: Contamination Source",
        (
            "Genuine foreign material typically traces to one of three sources: stencil-cleaning "
            "residue, operator handling (skin oils, fibers), or environmental particulates in the "
            "assembly area. If the same material type recurs across boards from the same shift, "
            "check environmental controls and stencil-cleaning cadence, not just the individual board."
        ),
    ),
    (
        "Foreign Material",
        "Foreign Material vs. False Positive",
        (
            "AOI systems commonly misflag flux residue, dust, and fingerprints as foreign material, "
            "since all three change local surface reflectance the same way an actual particle would. "
            "Cross-reference against recent false-positive patterns for this recipe before escalating "
            "as a confirmed contamination defect."
        ),
    ),
    (
        "Misalignment",
        "Misalignment: Immediate Handling",
        (
            "A misaligned component has landed offset from its intended pad registration. Check "
            "whether the offset is severe enough to also risk tombstoning or an open joint on reflow "
            "- if so, treat with the same quarantine caution as those defects, not just as a cosmetic "
            "placement issue."
        ),
    ),
    (
        "Misalignment",
        "Misalignment Root Cause: Placement Machine Calibration",
        (
            "Systematic misalignment in a consistent direction across many boards points to "
            "pick-and-place nozzle wear or vision-system calibration drift, not an isolated placement "
            "error. Check the machine's calibration log and vision alignment routine before assuming "
            "an operator or feeder issue."
        ),
    ),
    (
        "Misalignment",
        "Misalignment Root Cause: Component Feeder",
        (
            "A skewed tape-and-reel feeder or a worn feeder mechanism can cause a consistent rotational "
            "or positional offset for one specific component/feeder slot, while other positions on the "
            "same board remain correctly placed. If misalignment is isolated to one component type, "
            "inspect that feeder before the machine's overall calibration."
        ),
    ),
    (
        "Misalignment",
        "Misalignment Severity Thresholds",
        (
            "Not every registration offset requires rework - many process specifications (e.g. "
            "IPC-A-610) define an acceptable offset as a percentage of pad/lead width before a "
            "placement is considered defective. Compare the measured offset against the applicable "
            "acceptance criterion before routing to rework."
        ),
    ),
]


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        already_seeded = (await session.execute(select(RemediationDoc.id).limit(1))).first()
        if already_seeded:
            print("remediation_docs already seeded - skipping")
            return

        provider = get_embedding_provider()
        for defect_label, title, content in _DOCS:
            embedding = provider.embed(f"{title}. {content}")
            session.add(
                RemediationDoc(
                    defect_label=defect_label, title=title, content=content, embedding=embedding
                )
            )
        await session.commit()
        print(f"seeded {len(_DOCS)} remediation docs")


if __name__ == "__main__":
    asyncio.run(main())
