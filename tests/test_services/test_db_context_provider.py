"""Tests for the database-backed conversation context source (ADR-01).

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.database.models import Message
from src.services.messaging.context_provider import DatabaseContextProvider

# SQLite's CURRENT_TIMESTAMP has one-second granularity, so messages inserted in
# the same tick would tie and fall back to a random UUID for ordering. Timestamps
# are set explicitly here, as the existing history tests do.
BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def add_message(
    session, *, conversation_id, sender_id, text, client_message_id, minute=0
):
    """Persist one message at a distinct timestamp and return it."""
    message = Message(
        client_message_id=client_message_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text=text,
        source_language="vi",
        created_at=BASE_TIME + timedelta(minutes=minute),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


@pytest.mark.asyncio
async def test_returns_history_oldest_first(test_db, test_user, test_user_two, conversation_factory):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    for index, text in enumerate(["first", "second", "third"], start=1):
        await add_message(
            test_db,
            conversation_id=conversation.id,
            sender_id=test_user.id,
            text=text,
            client_message_id=f"c{index}",
            minute=index,
        )

    lines = await DatabaseContextProvider(test_db).get_recent_messages(conversation.id)

    assert [line.split(": ", 1)[1] for line in lines] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_respects_the_limit_and_keeps_the_newest(
    test_db, test_user, test_user_two, conversation_factory
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    for index in range(1, 6):
        await add_message(
            test_db,
            conversation_id=conversation.id,
            sender_id=test_user.id,
            text=f"message {index}",
            client_message_id=f"c{index}",
            minute=index,
        )

    lines = await DatabaseContextProvider(test_db).get_recent_messages(conversation.id, limit=2)

    assert [line.split(": ", 1)[1] for line in lines] == ["message 4", "message 5"]


@pytest.mark.asyncio
async def test_never_reads_across_conversation_boundaries(
    test_db, test_user, test_user_two, conversation_factory
):
    """The prompt is where one conversation could bleed into another (ADR-21).

    Everything the model is shown comes from here, so this filter is the whole
    isolation guarantee — an agent that saw the wrong thread would leak it into
    a translation no output guardrail could recognise as foreign.
    """
    theirs = await conversation_factory(test_user, [test_user, test_user_two])
    mine = await conversation_factory(test_user, [test_user, test_user_two])
    await add_message(
        test_db,
        conversation_id=theirs.id,
        sender_id=test_user_two.id,
        text="lương tháng này chuyển vào 0912345678",
        client_message_id="other-1",
        minute=1,
    )
    await add_message(
        test_db,
        conversation_id=mine.id,
        sender_id=test_user.id,
        text="deploy xong chưa",
        client_message_id="mine-1",
        minute=2,
    )

    lines = await DatabaseContextProvider(test_db).get_recent_messages(mine.id)

    assert [line.split(": ", 1)[1] for line in lines] == ["deploy xong chưa"]


@pytest.mark.asyncio
async def test_excludes_the_message_being_translated(
    test_db, test_user, test_user_two, conversation_factory
):
    """A message must not appear in its own context, or the model sees it twice."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    await add_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="earlier",
        client_message_id="c1",
        minute=1,
    )
    current = await add_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="the one being translated",
        client_message_id="c2",
        minute=2,
    )

    provider = DatabaseContextProvider(test_db, before_message_id=current.id)
    lines = await provider.get_recent_messages(conversation.id)

    assert [line.split(": ", 1)[1] for line in lines] == ["earlier"]


@pytest.mark.asyncio
async def test_labels_speakers_in_order_of_first_appearance(
    test_db, test_user, test_user_two, conversation_factory
):
    """Matches the U01/U02 convention the golden set is written against."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    speakers = [test_user, test_user_two, test_user]
    for index, sender in enumerate(speakers, start=1):
        await add_message(
            test_db,
            conversation_id=conversation.id,
            sender_id=sender.id,
            text=f"line {index}",
            client_message_id=f"c{index}",
            minute=index,
        )

    lines = await DatabaseContextProvider(test_db).get_recent_messages(conversation.id)

    assert lines == ["U01: line 1", "U02: line 2", "U01: line 3"]


@pytest.mark.asyncio
async def test_returns_nothing_for_an_unknown_conversation(test_db):
    provider = DatabaseContextProvider(test_db)

    assert await provider.get_recent_messages("does-not-exist") == []


@pytest.mark.asyncio
async def test_returns_nothing_without_a_usable_request(test_db):
    provider = DatabaseContextProvider(test_db)

    assert await provider.get_recent_messages("") == []
    assert await provider.get_recent_messages("c1", limit=0) == []


@pytest.mark.asyncio
async def test_withdrawn_message_is_kept_out_of_the_context(
    test_db, test_user, test_user_two, conversation_factory
):
    """A withdrawn message is unreadable in the app; it must be unreadable here too."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    withdrawn = await add_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="số thẻ của tôi là 9704 1234 5678 9010",
        client_message_id="c1",
        minute=1,
    )
    withdrawn.deleted_at = BASE_TIME + timedelta(minutes=2)
    await test_db.commit()
    await add_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        text="ok em nhận được rồi",
        client_message_id="c2",
        minute=3,
    )

    lines = await DatabaseContextProvider(test_db).get_recent_messages(conversation.id)

    assert [line.split(": ", 1)[1] for line in lines] == ["ok em nhận được rồi"]


@pytest.mark.asyncio
async def test_attachment_only_message_contributes_no_context_line(
    test_db, test_user, test_user_two, conversation_factory
):
    """An empty line spends tokens and tells the model nothing."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    await add_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="",
        client_message_id="c1",
        minute=1,
    )
    await add_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="ảnh chụp màn hình lỗi build",
        client_message_id="c2",
        minute=2,
    )

    lines = await DatabaseContextProvider(test_db).get_recent_messages(conversation.id)

    assert [line.split(": ", 1)[1] for line in lines] == ["ảnh chụp màn hình lỗi build"]
