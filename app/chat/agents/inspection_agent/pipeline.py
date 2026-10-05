"""The inspect agent's pipeline: an optional LLM-driven ReAct pass (react.py), then the fixed steps
the pass left undone, then the verdict, then a draft of the Case. The deterministic half is what
makes the outcome - the LLM can only choose the order work happens in and explain it afterwards.

No Case is saved here: the user is asked whether they want one, and the `create_case` tool commits
the draft only after they say yes (case_creation.py)."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import drafts, policy, react, steps
from app.chat.agents.inspection_agent.state import InspectionRequest, InspectionRun
from app.chat.agents.inspection_agent.verdict import Verdict, decide
from app.chat.db.models import CaseDraft
from app.shared.config.langfuse import traced
from app.shared.config.settings import settings


@dataclass
class InspectionOutcome:
    run: InspectionRun
    verdict: Verdict | None = None
    # The inspection parked for the user's yes; None when the run failed before a verdict.
    draft: CaseDraft | None = None
    # The ReAct pass's closing summary; None when the LLM pass was off, failed, or said nothing.
    summary: str | None = None


@traced("inspection")
async def run_inspection(
    session: AsyncSession, request: InspectionRequest, *, question: str | None = None
) -> InspectionOutcome:
    """Never raises for expected failures: an unreadable image or an unreachable inference service
    comes back as an outcome with `run.error` set and no draft. Persistence errors do propagate."""

    run = InspectionRun(request=request)

    summary: str | None = None
    if _llm_pass_enabled():
        summary = await react.run_react_pass(run, question=question)

    await _complete(run)
    if run.error:
        return InspectionOutcome(run, summary=summary)

    verdict = decide(run)
    run.note(f"Workflow finalized as {verdict.status.value}.")
    draft = await drafts.save_draft(
        session,
        conversation_id=run.request.conversation_id,
        user_id=run.request.user_id,
        payload=_case_fields(run, verdict),
    )
    return InspectionOutcome(run, verdict, draft, summary)


def _llm_pass_enabled() -> bool:
    return bool(settings.inspection_agent_llm_enabled and settings.openai_api_key)


async def _complete(run: InspectionRun) -> None:
    """Runs every step the LLM pass left undone, in order, through the same policy it was held
    to. With no LLM pass this is the whole inspection."""

    for step in policy.STEPS:
        if run.error:
            return
        if policy.is_done(step, run):
            continue
        allowed, _ = policy.check(step, run)
        if allowed:  # otherwise not applicable (no XML) or not reachable (uncertain region)
            await steps.execute(step, run)


def _case_fields(run: InspectionRun, verdict: Verdict) -> dict[str, Any]:
    """What `cases.create_case` is given (besides user and conversation) if the user wants this
    inspection kept as a Case."""

    request, region, defect = run.request, run.region, run.defect
    return {
        "board_id": request.board_id,
        "component_ref": request.component_ref,
        "package": request.package,
        "feature": request.feature,
        "issue_symptom": request.issue_symptom,
        "image_id": request.image_name,
        "inspection_xml_id": request.inspection_xml_id,
        "region": region.label if region else None,
        "region_confidence": region.confidence if region else None,
        "region_model_version": region.model_version if region else None,
        "defect_model": defect.model if defect else None,
        "defect_model_version": defect.model_version if defect else None,
        "defect_label": defect.label if defect else None,
        "defect_confidence": defect.confidence if defect else None,
        "defect_scores": defect.scores if defect else {},
        "measurement_validation": run.measurement_validation,
        "observations": run.observations,
        "status": verdict.status.value,
    }
