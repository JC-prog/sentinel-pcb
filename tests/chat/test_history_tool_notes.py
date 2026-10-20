"""History replays text only, so a card a tool produced is carried into later turns as a short note."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Conversation
from app.chat.services import history
from tests.shared._modelops_helpers import make_user


async def _conversation(db_async_session: AsyncSession) -> Conversation:
    user = await make_user(db_async_session)
    conversation, _ = await history.get_or_create_conversation(db_async_session, user.id, "conv-notes")
    return conversation


async def test_an_assistant_turn_carries_a_note_of_its_cards(db_async_session: AsyncSession) -> None:
    conversation = await _conversation(db_async_session)
    await history.append_message(db_async_session, conversation.id, "user", "relabel it", [])
    await history.append_message(
        db_async_session,
        conversation.id,
        "assistant",
        "I would record Open.",
        [],
        tool_results=[
            {
                "name": "relabel_case",
                "result": {"case_number": "CASE-000007", "proposed_label": "Open", "junk": 1},
            }
        ],
    )

    turns = await history.load_history(db_async_session, conversation.id, max_turns=10)

    assert turns[0].content == "relabel it"
    assert "relabel_case(case_number=CASE-000007, proposed_label=Open)" in turns[1].content
    assert "junk" not in turns[1].content


async def test_a_turn_without_cards_is_replayed_unchanged(db_async_session: AsyncSession) -> None:
    conversation = await _conversation(db_async_session)
    await history.append_message(db_async_session, conversation.id, "assistant", "hello", [])

    [turn] = await history.load_history(db_async_session, conversation.id, max_turns=10)

    assert turn.content == "hello"
