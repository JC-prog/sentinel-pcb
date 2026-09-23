"""The case agent's everyday tools: look cases up, find ones similar to another, and record a
reviewer's decision. (tools.py beside this holds the heavyweight diagnosis tools.)

None of these takes an image or anything an LLM could invent an id for - they work from case
numbers, or from "the case in this conversation", which the caller (app/chat/services/streaming.py's
_run_tool_call) resolves by injecting `session`, `user_id` and `conversation_id`.
"""

import json
from typing import Any

from app.chat.db.models import CaseStatus
from app.chat.services.cases import (
    case_summary,
    find_similar_cases,
    get_case_by_sequence_number,
    latest_case_in_conversation,
    list_cases,
    parse_case_number,
    resolve_case,
)

_MAX_SIMILAR = 10


class FindSimilarCasesTool:
    name = "find_similar_cases"
    description = (
        "Finds earlier cases similar to a given one - same defect type first, then same region, "
        "component, package and board, then closest classifier confidence - and says why each "
        "matched. Use it for questions like 'have we seen this defect before?' or 'show me "
        "similar cases'. Without a case number it uses the most recent case from this "
        "conversation (the image the user just had inspected)."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "case_number": {
                    "type": "string",
                    "description": (
                        'The case to compare against, e.g. "CASE-000123". Omit to use the '
                        "latest case in this conversation."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many similar cases to return (1-{_MAX_SIMILAR}).",
                    "default": 5,
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        limit = max(1, min(int(kwargs.get("limit") or 5), _MAX_SIMILAR))

        case_number = (kwargs.get("case_number") or "").strip()
        if case_number:
            sequence_number = parse_case_number(case_number)
            if sequence_number is None:
                return json.dumps({"error": f"invalid case number: {case_number!r}"})
            reference = await get_case_by_sequence_number(session, sequence_number)
            if reference is None:
                return json.dumps({"error": "case not found"})
        else:
            reference = await latest_case_in_conversation(session, kwargs["conversation_id"])
            if reference is None:
                return json.dumps(
                    {
                        "error": (
                            "no case in this conversation yet - inspect an image first, or "
                            "give a case number"
                        )
                    }
                )

        if not reference.defect_label and not reference.region:
            return json.dumps(
                {
                    "error": (
                        f"{reference.case_number} has no classification to compare on "
                        "(inspection did not get as far as a region or defect)"
                    )
                }
            )

        matches = await find_similar_cases(session, reference, limit=limit)
        return json.dumps(
            {
                "compared_against": case_summary(reference),
                "similar_cases": [
                    {**case_summary(m.case), "similarity": m.score, "why_similar": m.reasons}
                    for m in matches
                ],
                "note": (
                    "Ranked by matching defect/region/component/package/board and confidence "
                    "proximity over cases recorded in this app."
                ),
            }
        )


class GetCaseTool:
    name = "get_case"
    description = (
        "Shows the full record of one case by case number: its verdict and confidence, the "
        "model versions that produced it, measurement validation, review status and notes, "
        "and the inspection steps that were taken."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "case_number": {
                    "type": "string",
                    "description": 'The case number, e.g. "CASE-000123".',
                }
            },
            "required": ["case_number"],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        case_number = kwargs.get("case_number") or ""
        sequence_number = parse_case_number(case_number)
        if sequence_number is None:
            return json.dumps({"error": f"invalid case number: {case_number!r}"})

        case = await get_case_by_sequence_number(session, sequence_number)
        if case is None:
            return json.dumps({"error": "case not found"})

        return json.dumps(
            {
                **case_summary(case),
                "issue_symptom": case.issue_symptom,
                "defect_model": case.defect_model,
                "region_confidence": case.region_confidence,
                "defect_scores": case.defect_scores,
                "measurement_validation": case.measurement_validation,
                "resolution_note": case.resolution_note,
                "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
                "observations": case.observations,
            }
        )


class ListCasesTool:
    name = "list_cases"
    description = "Lists Cases, most recent first, optionally filtered by review status."

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [s.value for s in CaseStatus],
                    "description": "Filter by status. Omit to list every status.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of cases to return.",
                    "default": 20,
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        status_arg = kwargs.get("status")
        status = CaseStatus(status_arg) if status_arg else None
        limit = int(kwargs.get("limit") or 20)

        cases = await list_cases(session, status=status, limit=limit)
        return json.dumps({"cases": [case_summary(case) for case in cases]})


class ReviewCaseTool:
    name = "review_case"
    description = (
        "Resolves a REVIEW_REQUIRED case by approving the flagged defect or overriding it as a "
        "false positive."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "case_number": {
                    "type": "string",
                    "description": 'The case number, e.g. "CASE-000123".',
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "override"],
                    "description": (
                        "'approve' confirms the flagged defect stands; 'override' reverses it as "
                        "a false positive."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "Optional reviewer note explaining the decision.",
                },
            },
            "required": ["case_number", "decision"],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        case_number = kwargs.get("case_number") or ""
        decision = kwargs.get("decision")

        sequence_number = parse_case_number(case_number)
        if sequence_number is None:
            return json.dumps({"error": f"invalid case number: {case_number!r}"})

        case = await get_case_by_sequence_number(session, sequence_number)
        if case is None:
            return json.dumps({"error": "case not found"})

        if case.status != CaseStatus.REVIEW_REQUIRED:
            return json.dumps({"error": "case is not pending review"})

        if decision == "approve":
            new_status = CaseStatus.APPROVED
        elif decision == "override":
            new_status = CaseStatus.OVERRIDDEN
        else:
            return json.dumps({"error": f"invalid decision: {decision!r}"})

        updated = await resolve_case(
            session,
            case,
            status=new_status,
            resolved_by_user_id=kwargs["user_id"],
            note=kwargs.get("note"),
        )
        return json.dumps(case_summary(updated))
