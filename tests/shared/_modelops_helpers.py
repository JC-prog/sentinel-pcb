"""Row factories for the model-operations tests. Tickets reference real cases (and cases a real
user and conversation), so even a "shared" test needs a little chat-side scaffolding."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, Conversation
from app.chat.services.cases import create_case
from app.shared.db.models import RetrainingTicket, User, UserRole
from app.shared.inference import JobArtifact, JobResult, ModelInfo, RemoteJob
from app.shared.modelops.tickets import create_ticket

_counter = 0


def _next() -> int:
    global _counter
    _counter += 1
    return _counter


async def make_user(session: AsyncSession, role: UserRole = UserRole.QA) -> User:
    n = _next()
    user = User(
        username=f"user-{n}",
        email=f"user-{n}@example.com",
        password_hash="x",
        employee_id=f"EMP-{n}",
        department_shift="QA Day Shift",
        role=role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def make_case(session: AsyncSession, user: User) -> Case:
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return await create_case(
        session,
        created_by_user_id=user.id,
        conversation_id=conversation.id,
        board_id="BOARD-1",
        component_ref="U7",
        package=None,
        feature=None,
        issue_symptom=None,
        image_id="board.png",
        inspection_xml_id=None,
        golden_image_id=None,
        region="Body",
        region_confidence=0.9,
        defect_model="pcb_body_defect",
        defect_model_version="JcProg/body@v1",
        defect_label="MissingPart",
        defect_confidence=0.9,
        defect_scores={},
        measurement_validation=None,
        observations=[],
        status="accepted",
    )


async def make_ticket(
    session: AsyncSession,
    user: User,
    *,
    model_name: str | None = "pcb_body_defect",
    model_version: str | None = "JcProg/body@v1",
    correct_label: str | None = "Golden",
) -> RetrainingTicket:
    case = await make_case(session, user)
    return await create_ticket(
        session,
        case_id=case.id,
        case_number=case.case_number,
        flagged_by_user_id=user.id,
        reason="false positive",
        model_name=model_name,
        model_version=model_version,
        observed_label="MissingPart",
        correct_label=correct_label,
    )


def model_info(name: str, version: str, previous: str | None = None) -> ModelInfo:
    return ModelInfo(
        name=name,
        version=version,
        previous_version=previous,
        loaded_at=datetime.now(UTC),
        labels=["a", "b"],
        input_size=(224, 224),
    )


def remote_job(
    status: str,
    *,
    remote_id: str = "remote-1",
    progress: float = 0.0,
    error: str | None = None,
    artifact: tuple[str, str] | None = None,
    simulated: bool = True,
) -> RemoteJob:
    now = datetime.now(UTC)
    return RemoteJob(
        id=remote_id,
        client_ref="ref",
        model="pcb_body_defect",
        base_version="JcProg/body@v1",
        status=status,
        progress=progress,
        sample_count=1,
        created_at=now,
        started_at=now if status != "queued" else None,
        finished_at=now if status in ("succeeded", "failed", "cancelled") else None,
        error=error,
        result=(
            JobResult(
                simulated=simulated,
                artifact=JobArtifact(repo_id=artifact[0], revision=artifact[1]),
            )
            if artifact is not None
            else None
        ),
    )
