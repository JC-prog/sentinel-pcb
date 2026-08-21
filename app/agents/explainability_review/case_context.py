"""Case Context retrieval - the RAG tool Explainability & Review calls to answer "why is this a
defect, and what should happen next." Resolves the previously-open PRD §5/§11 question (tool vs.
subagent): this is squarely a tool, invoked from within Explainability & Review's own turn, never
owning a disposition of its own.

Retrieves from two sources, both embedded the same way (app.services.embeddings):
  - RemediationDoc: a hand-authored synthetic corpus of "how to handle defect X" guidance
    (scripts/seed_remediation_docs.py) - answers "what should happen next."
  - Workflow.embedding: our own accumulating history of auto-accepted cases - answers "have we
    seen something like this before." Thin today (few real cases exist yet), grows over time.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RemediationDoc, Workflow
from app.services.embeddings import get_embedding_provider


class CaseContext(BaseModel):
    precedents: list[dict[str, object]]
    guidance: list[dict[str, object]]


async def retrieve_context(
    session: AsyncSession, query_text: str, current_workflow_id: str, k: int = 3
) -> CaseContext:
    query_embedding = get_embedding_provider().embed(query_text)

    guidance_stmt = (
        select(RemediationDoc)
        .order_by(RemediationDoc.embedding.cosine_distance(query_embedding))
        .limit(k)
    )
    guidance_rows = (await session.execute(guidance_stmt)).scalars().all()

    precedent_stmt = (
        select(Workflow)
        .where(Workflow.embedding.is_not(None), Workflow.id != current_workflow_id)
        .order_by(Workflow.embedding.cosine_distance(query_embedding))
        .limit(k)
    )
    precedent_rows = (await session.execute(precedent_stmt)).scalars().all()

    return CaseContext(
        guidance=[
            {"title": doc.title, "defect_label": doc.defect_label, "content": doc.content}
            for doc in guidance_rows
        ],
        precedents=[
            {
                "workflow_id": wf.id,
                "board_id": wf.board_id,
                "component_id": wf.component_id,
                "defect_label": wf.defect_label,
                "decision": wf.decision,
            }
            for wf in precedent_rows
        ],
    )
