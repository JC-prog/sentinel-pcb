"""The inspect agent's pipeline: an optional LLM-driven ReAct pass (react.py), then the fixed steps
the pass left undone, then the verdict, then the Case. The deterministic half is what makes the
outcome - the LLM can only choose the order work happens in and explain it afterwards."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import policy, react, steps
from app.chat.agents.inspection_agent.state import InspectionRequest, InspectionRun
from app.chat.agents.inspection_agent.verdict import Verdict, decide
from app.chat.db.models import Case
from app.chat.services import cases
from app.shared.config.langfuse import traced
from app.shared.config.settings import settings


@dataclass
class InspectionOutcome:
    run: InspectionRun
    verdict: Verdict | None = None
    case: Case | None = None
    # The ReAct pass's closing summary; None when the LLM pass was off, failed, or said nothing.
    summary: str | None = None


@traced("inspection")
async def run_inspection(
    session: AsyncSession, request: InspectionRequest, *, question: str | None = None
) -> InspectionOutcome:
    """Never raises for expected failures: an unreadable image or an unreachable inference service
    comes back as an outcome with `run.error` set and no case. Persistence errors do propagate."""

    run = InspectionRun(request=request)

    summary: str | None = None
    if _llm_pass_enabled():
        summary = await react.run_react_pass(run, question=question)

    await _complete(run)
    if run.error:
        return InspectionOutcome(run, summary=summary)

    verdict = decide(run)
    run.note(f"Workflow finalized as {verdict.status.value}.")
    case = await _save_case(session, run, verdict)
    return InspectionOutcome(run, verdict, case, summary)


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


async def _save_case(session: AsyncSession, run: InspectionRun, verdict: Verdict) -> Case:
    request, region, defect = run.request, run.region, run.defect
    return await cases.create_case(
        session,
        created_by_user_id=request.user_id,
        conversation_id=request.conversation_id,
        board_id=request.board_id,
        component_ref=request.component_ref,
        package=request.package,
        feature=request.feature,
        issue_symptom=request.issue_symptom,
        image_id=request.image_name,
        inspection_xml_id=request.inspection_xml_id,
        region=region.label if region else None,
        region_confidence=region.confidence if region else None,
        region_model_version=region.model_version if region else None,
        defect_model=defect.model if defect else None,
        defect_model_version=defect.model_version if defect else None,
        defect_label=defect.label if defect else None,
        defect_confidence=defect.confidence if defect else None,
        defect_scores=defect.scores if defect else {},
        measurement_validation=run.measurement_validation,
        observations=run.observations,
        status=verdict.status.value,
    )
