"""Tests for retrieving conversation context by meaning as well as by clock.

The privacy cases matter most. A second way into `messages` is a second way for
a withdrawn message to reach the model, and a second chance to pull text out of
a conversation the reader was never part of — and neither failure announces
itself, because both produce a perfectly ordinary translation.

Embeddings are hand-written rather than generated: what is under test is the
retrieval and the filters, and a real model would make the assertions depend on
its opinions.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.database.models import EMBEDDING_DIM, Message, MessageEmbedding
from src.services.messaging import message_memory
from src.services.messaging.context_provider import DatabaseContextProvider
from src.services.messaging.message_memory import schedule_message_embedding

VALID_SECRET = "x" * 48
BASE = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


def settings(**overrides) -> Settings:
    """Build settings without the developer's own .env deciding the outcome."""
    return Settings(jwt_secret=VALID_SECRET, **overrides)


def vector(*leading: float) -> list[float]:
    """Pad a few meaningful components out to the column's fixed width."""
    return [*leading] + [0.0] * (EMBEDDING_DIM - len(leading))


async def add_message(
    test_db: AsyncSession,
    conversation_id: str,
    sender_id: str,
    text: str,
    *,
    minute: int,
    embedding: list[float] | None = None,
    withdrawn: bool = False,
) -> Message:
    """Store one message, optionally with a vector and optionally withdrawn."""
    message = Message(
        client_message_id=f"m-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text=text,
        source_language="en",
        created_at=BASE + timedelta(minutes=minute),
        deleted_at=BASE + timedelta(minutes=minute + 1) if withdrawn else None,
    )
    test_db.add(message)
    await test_db.flush()
    if embedding is not None:
        test_db.add(
            MessageEmbedding(
                message_id=message.id,
                conversation_id=conversation_id,
                embedding=embedding,
                embedding_model="test-embedder",
            )
        )
    await test_db.commit()
    return message


@pytest_asyncio.fixture
async def thread(test_db: AsyncSession, test_user, test_user_two, conversation_factory):
    """A conversation whose oldest message is the one worth recalling.

    The recent window is three lines of small talk; the decision everybody is
    referring to was made long before them, which is exactly the shape the
    time-ordered window cannot serve.
    """
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="group"
    )
    old = await add_message(
        test_db,
        conversation.id,
        test_user_two.id,
        "We agreed to run the migration on Friday night",
        minute=0,
        embedding=vector(1.0),
    )
    for index, text in enumerate(("morning", "coffee first", "ok"), start=10):
        await add_message(
            test_db,
            conversation.id,
            test_user.id,
            text,
            minute=index,
            embedding=vector(0.0, 1.0),
        )
    current = await add_message(
        test_db,
        conversation.id,
        test_user.id,
        "Is the migration still on?",
        minute=30,
        embedding=vector(0.99, 0.1),
    )
    return conversation, old, current


async def context_for(test_db, conversation, current, **overrides) -> list[str]:
    """Read the context the agent would be given for `current`."""
    provider = DatabaseContextProvider(
        test_db, current.id, settings=settings(**overrides)
    )
    return await provider.get_recent_messages(conversation.id, limit=3)


@pytest.mark.asyncio
async def test_only_the_recent_window_is_used_when_recall_is_off(
    test_db, thread
):
    """Default behaviour, unchanged: three lines of small talk and nothing else."""
    conversation, old, current = thread

    lines = await context_for(test_db, conversation, current)

    assert len(lines) == 3
    assert not any("migration" in line for line in lines)


@pytest.mark.asyncio
async def test_an_older_message_is_recalled_when_its_meaning_matches(
    test_db, thread
):
    """The reason the feature exists: the message says "the migration", and what
    that refers to is forty lines out of reach of the time-ordered window."""
    conversation, old, current = thread

    lines = await context_for(
        test_db, conversation, current, rag_context_enabled=True
    )

    assert any("migration on Friday night" in line for line in lines)


@pytest.mark.asyncio
async def test_recalled_lines_are_merged_into_one_history_in_time_order(
    test_db, thread
):
    """The model is told the block is oldest-first and nothing else. Which path
    a line arrived by is not something a translation should reason about."""
    conversation, old, current = thread

    lines = await context_for(
        test_db, conversation, current, rag_context_enabled=True
    )

    assert "migration on Friday night" in lines[0]
    assert lines[-1].endswith("ok")


@pytest.mark.asyncio
async def test_a_withdrawn_message_never_returns_through_recall(
    test_db, test_user, test_user_two, conversation_factory
):
    """The sender withdrew it, so nobody can read it in the app any more. A
    second retrieval path must honour the same boundary as the first — and it is
    the harder one to notice, because the line arrives looking like any other."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="group"
    )
    await add_message(
        test_db,
        conversation.id,
        test_user_two.id,
        "The production password is hunter2",
        minute=0,
        embedding=vector(1.0),
        withdrawn=True,
    )
    current = await add_message(
        test_db,
        conversation.id,
        test_user.id,
        "What was that credential again",
        minute=30,
        embedding=vector(0.99, 0.1),
    )

    lines = await context_for(
        test_db, conversation, current, rag_context_enabled=True
    )

    assert lines == []


@pytest.mark.asyncio
async def test_recall_never_reaches_into_another_conversation(
    test_db, test_user, test_user_two, conversation_factory
):
    """Retrieval crossing conversations would take text from a thread the reader
    was never part of and put it in front of the model — the leak ADR-21 catches
    on the way out, introduced on the way in."""
    mine = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="group"
    )
    theirs = await conversation_factory(
        test_user_two, [test_user_two], conversation_type="group"
    )
    await add_message(
        test_db,
        theirs.id,
        test_user_two.id,
        "Our margin on that deal was thirty percent",
        minute=0,
        embedding=vector(1.0),
    )
    current = await add_message(
        test_db,
        mine.id,
        test_user.id,
        "What is the margin",
        minute=30,
        embedding=vector(0.99, 0.1),
    )

    lines = await context_for(test_db, mine, current, rag_context_enabled=True)

    assert lines == []


@pytest.mark.asyncio
async def test_recall_does_not_repeat_a_line_the_window_already_has(
    test_db, test_user, conversation_factory
):
    """The recent window is usually among the nearest neighbours too. Sending a
    line twice teaches the model that the repetition is emphasis."""
    conversation = await conversation_factory(
        test_user, [test_user], conversation_type="group"
    )
    await add_message(
        test_db,
        conversation.id,
        test_user.id,
        "the only other line",
        minute=1,
        embedding=vector(1.0),
    )
    current = await add_message(
        test_db,
        conversation.id,
        test_user.id,
        "and this one",
        minute=2,
        embedding=vector(1.0),
    )

    lines = await context_for(
        test_db, conversation, current, rag_context_enabled=True
    )

    assert lines == ["U01: the only other line"]


@pytest.mark.asyncio
async def test_a_message_sent_before_recall_was_switched_on_recalls_nothing(
    test_db, test_user, conversation_factory
):
    """No stored vector means no query vector. Every message predating the flag
    is in this state, so it has to degrade to the old behaviour rather than
    raise."""
    conversation = await conversation_factory(
        test_user, [test_user], conversation_type="group"
    )
    await add_message(
        test_db, conversation.id, test_user.id, "older", minute=0, embedding=vector(1.0)
    )
    current = await add_message(
        test_db, conversation.id, test_user.id, "current", minute=30
    )

    lines = await context_for(
        test_db, conversation, current, rag_context_enabled=True
    )

    assert lines == ["U01: older"]


@pytest.mark.asyncio
async def test_nothing_is_embedded_while_recall_is_switched_off(test_db):
    """Embedding every message for a feature nobody turned on spends real quota
    on nothing."""
    schedule_message_embedding(
        message_id="m1",
        conversation_id="c1",
        text="hello",
        settings=settings(rag_context_enabled=False),
    )

    assert not message_memory._BACKGROUND_TASKS


@pytest.mark.asyncio
async def test_the_assistants_memory_consent_remembers_a_message_on_its_own(
    test_db, test_user, conversation_factory, monkeypatch
):
    """Two independent reasons to embed, and this is the second one.

    `rag_context_enabled` serves the translation agent (ADR-27); the sender's
    `store_memory` consent serves the assistant (ADR-30). Turning one on must
    not require the other, or enabling the assistant would silently switch on
    semantic context for every translation as well.
    """
    from src.services.assistant.agent_consent import set_consents

    conversation = await conversation_factory(test_user, [test_user], conversation_type="group")
    message = await add_message(test_db, conversation.id, test_user.id, "remember me", minute=0)
    await set_consents(test_db, test_user.id, {"store_memory": True})

    async def fixed(text, *, settings=None):
        return vector(0.0, 1.0), "test-embedding-model"

    monkeypatch.setattr("src.services.messaging.message_memory.embed_with_model", fixed)

    import tests.conftest as conftest_module

    await message_memory._store(
        message_id=message.id,
        conversation_id=conversation.id,
        text="remember me",
        sender_id=test_user.id,
        session_factory=conftest_module.test_async_session_maker,
        settings=settings(rag_context_enabled=False),
    )

    stored = await test_db.scalar(
        select(MessageEmbedding).where(MessageEmbedding.message_id == message.id)
    )
    assert stored is not None


@pytest.mark.asyncio
async def test_a_message_is_never_embedded_when_its_sender_withheld_memory_consent(
    test_db, test_user, conversation_factory, monkeypatch
):
    """The refusal has to bite before the text reaches the embedding provider.

    Asserting the embedder was never called, not merely that no row was written:
    sending the message out and then discarding the answer would still have
    handed the text to a third party (ADR-15).
    """
    conversation = await conversation_factory(test_user, [test_user], conversation_type="group")
    message = await add_message(test_db, conversation.id, test_user.id, "private", minute=0)

    calls: list[str] = []

    async def recording(text, *, settings=None):
        calls.append(text)
        return vector(0.0, 1.0), "test-embedding-model"

    monkeypatch.setattr("src.services.messaging.message_memory.embed_with_model", recording)

    import tests.conftest as conftest_module

    await message_memory._store(
        message_id=message.id,
        conversation_id=conversation.id,
        text="private",
        sender_id=test_user.id,
        session_factory=conftest_module.test_async_session_maker,
        settings=settings(rag_context_enabled=False),
    )

    assert calls == []
    stored = await test_db.scalar(
        select(MessageEmbedding).where(MessageEmbedding.message_id == message.id)
    )
    assert stored is None


@pytest.mark.asyncio
async def test_remembering_a_message_replaces_the_vector_it_had(
    test_db, test_user, conversation_factory, monkeypatch
):
    """A message can be edited, and a vector describing wording it no longer has
    would retrieve it for reasons that are no longer true."""
    conversation = await conversation_factory(
        test_user, [test_user], conversation_type="group"
    )
    message = await add_message(
        test_db,
        conversation.id,
        test_user.id,
        "first wording",
        minute=0,
        embedding=vector(1.0),
    )

    async def fixed(text, *, settings=None):
        # Returns the pair  returns: the vector and the model
        # that produced it, which is what gets stored beside it.
        return vector(0.0, 1.0), "test-embedding-model"

    monkeypatch.setattr("src.services.messaging.message_memory.embed_with_model", fixed)

    import tests.conftest as conftest_module

    await message_memory._store(
        message_id=message.id,
        conversation_id=conversation.id,
        text="second wording",
        session_factory=conftest_module.test_async_session_maker,
        settings=settings(rag_context_enabled=True),
    )

    rows = (
        await test_db.scalars(
            select(MessageEmbedding)
            .where(MessageEmbedding.message_id == message.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert len(rows) == 1
    assert list(rows[0].embedding)[:2] == [0.0, 1.0]
