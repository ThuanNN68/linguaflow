"""Tests for the constraints the audience and honorific schema relies on.

These assert database behaviour rather than application behaviour, which is
unusual here and deliberate: each rule below is enforced by PostgreSQL, and the
code that depends on it reads as if the rule were free. A constraint dropped by
a careless migration would not fail any other test in the suite — the writes
would simply start succeeding, and the first symptom would be a reader seeing
somebody else's register.

Every test that expects a constraint to fire rolls the session back afterwards.
A commit that raised leaves the session unusable, so the shared `test_db`
fixture's own commit on teardown would raise PendingRollbackError and report the
test as an error immediately after it had proved its point.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Conversation,
    GlossaryEntry,
    Message,
    MessageEmbedding,
    ParticipantProfile,
    TranslationResult,
    User,
)


@pytest_asyncio.fixture
async def owner(test_db: AsyncSession) -> User:
    """An account to hang conversations and messages off."""
    user = User(
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="member",
        preferred_language="vi",
        interface_language="vi",
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest_asyncio.fixture
async def message(test_db: AsyncSession, owner: User) -> Message:
    """One stored message, the thing translations attach to."""
    conversation = Conversation(type="group", created_by=owner.id)
    test_db.add(conversation)
    await test_db.flush()

    stored = Message(
        client_message_id=f"c-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation.id,
        sender_id=owner.id,
        original_text="Anh check lai UI giup em",
        source_language="vi",
    )
    test_db.add(stored)
    await test_db.commit()
    await test_db.refresh(stored)
    return stored


def _translation(message_id: str, profile: str, text: str) -> TranslationResult:
    """A translation row differing from its siblings only in standing."""
    return TranslationResult(
        message_id=message_id,
        target_language="en",
        honorific_profile=profile,
        translated_text=text,
        model="test-model",
        latency_ms=1,
        is_fallback=False,
    )


@pytest.mark.asyncio
async def test_one_message_holds_a_separate_translation_per_standing(
    test_db: AsyncSession, message: Message
) -> None:
    """The whole point of widening the key: two registers, one language."""
    test_db.add(_translation(message.id, "junior", "Could you take a look?"))
    test_db.add(_translation(message.id, "client", "Would you kindly review this?"))
    await test_db.commit()

    rows = (
        await test_db.scalars(
            select(TranslationResult).where(TranslationResult.message_id == message.id)
        )
    ).all()

    assert {row.honorific_profile for row in rows} == {"junior", "client"}


@pytest.mark.asyncio
async def test_repeating_a_standing_for_one_language_is_rejected(
    test_db: AsyncSession, message: Message
) -> None:
    """The key is wider, not absent: a retry must still find the existing row."""
    test_db.add(_translation(message.id, "peer", "Please take a look"))
    await test_db.commit()

    test_db.add(_translation(message.id, "peer", "A second one"))
    with pytest.raises(IntegrityError):
        await test_db.commit()

    await test_db.rollback()  # see the note on rollbacks in the module docstring


@pytest.mark.asyncio
async def test_a_standing_outside_the_four_is_rejected(
    test_db: AsyncSession, message: Message
) -> None:
    """A typo'd standing must fail loudly, not create a fifth bucket nobody reads."""
    test_db.add(_translation(message.id, "colleague", "Please take a look"))
    with pytest.raises(IntegrityError):
        await test_db.commit()

    await test_db.rollback()  # see the note on rollbacks in the module docstring


@pytest.mark.asyncio
async def test_a_participant_holds_one_standing_per_conversation(
    test_db: AsyncSession, owner: User, message: Message
) -> None:
    """Re-inference has to update the row it finds, never add a rival to it."""
    test_db.add(
        ParticipantProfile(
            conversation_id=message.conversation_id,
            user_id=owner.id,
            honorific_profile="peer",
            inferred_by="llm",
            confidence=80,
        )
    )
    await test_db.commit()

    test_db.add(
        ParticipantProfile(
            conversation_id=message.conversation_id,
            user_id=owner.id,
            honorific_profile="senior",
            inferred_by="llm",
            confidence=90,
        )
    )
    with pytest.raises(IntegrityError):
        await test_db.commit()

    await test_db.rollback()  # see the note on rollbacks in the module docstring


def _entry(audience: str, target_term: str, keep_verbatim: bool) -> GlossaryEntry:
    """The same source term, scoped to one audience."""
    return GlossaryEntry(
        source_term="UI",
        source_term_normalized="ui",
        target_term=target_term,
        source_language="en",
        target_language="vi",
        domain="tech",
        audience=audience,
        keep_verbatim=keep_verbatim,
        status="active",
    )


@pytest.mark.asyncio
async def test_one_term_resolves_two_ways_for_two_audiences(
    test_db: AsyncSession,
) -> None:
    """The feature in one assertion: UI stays UI internally, becomes giao dien
    for a client."""
    test_db.add(_entry("internal", "UI", keep_verbatim=True))
    test_db.add(_entry("client", "giao dien", keep_verbatim=False))
    await test_db.commit()

    rows = (
        await test_db.scalars(
            select(GlossaryEntry).where(GlossaryEntry.source_term_normalized == "ui")
        )
    ).all()

    assert {row.audience: row.target_term for row in rows} == {
        "internal": "UI",
        "client": "giao dien",
    }


@pytest.mark.asyncio
async def test_repeating_a_term_within_one_audience_is_rejected(
    test_db: AsyncSession,
) -> None:
    """Two renderings for one scope would make the lookup pick arbitrarily."""
    test_db.add(_entry("client", "giao dien", keep_verbatim=False))
    await test_db.commit()

    test_db.add(_entry("client", "man hinh", keep_verbatim=False))
    with pytest.raises(IntegrityError):
        await test_db.commit()

    await test_db.rollback()  # see the note on rollbacks in the module docstring


@pytest.mark.asyncio
async def test_an_unscoped_term_coexists_with_a_scoped_one(
    test_db: AsyncSession,
) -> None:
    """Empty scope means "applies everywhere" and is the fallback, so it has to
    be storable alongside the scoped rows rather than colliding with them."""
    fallback = _entry("", "giao dien nguoi dung", keep_verbatim=False)
    fallback.domain = ""
    test_db.add(fallback)
    test_db.add(_entry("internal", "UI", keep_verbatim=True))
    await test_db.commit()

    rows = (
        await test_db.scalars(
            select(GlossaryEntry).where(GlossaryEntry.source_term_normalized == "ui")
        )
    ).all()

    assert len(rows) == 2


@pytest.mark.asyncio
async def test_deleting_a_message_takes_its_embedding_with_it(
    test_db: AsyncSession, message: Message
) -> None:
    """Derived data must not outlive its source, or retrieval keeps serving a
    message the conversation no longer contains."""
    test_db.add(
        MessageEmbedding(
            message_id=message.id,
            conversation_id=message.conversation_id,
            embedding=[0.0] * 768,
            embedding_model="test-embedder",
        )
    )
    await test_db.commit()

    await test_db.delete(message)
    await test_db.commit()

    remaining = (await test_db.scalars(select(MessageEmbedding))).all()
    assert remaining == []


@pytest.mark.asyncio
async def test_an_embedding_survives_a_translation_being_deleted(
    test_db: AsyncSession, message: Message
) -> None:
    """Only the message owns the vector. Re-translating a message deletes its
    translations, and that must not throw away work that costs an API call."""
    test_db.add(
        MessageEmbedding(
            message_id=message.id,
            conversation_id=message.conversation_id,
            embedding=[0.5] * 768,
            embedding_model="test-embedder",
        )
    )
    translation = _translation(message.id, "peer", "Please take a look")
    test_db.add(translation)
    await test_db.commit()

    await test_db.delete(translation)
    await test_db.commit()

    remaining = (await test_db.scalars(select(MessageEmbedding))).all()
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_text_message_database_defaults_remain_unchanged(
    message: Message,
) -> None:
    """Omitting lifecycle fields must keep every existing write a text write."""
    assert message.message_type == "text"
    assert message.transcription_status is None
    assert message.original_text == "Anh check lai UI giup em"


@pytest.mark.parametrize(
    ("status", "original_text"),
    [
        ("pending", ""),
        ("completed", "Đây là toàn bộ nội dung tin nhắn thoại."),
        ("failed", ""),
    ],
)
@pytest.mark.asyncio
async def test_database_accepts_each_valid_voice_lifecycle_state(
    test_db: AsyncSession,
    message: Message,
    status: str,
    original_text: str,
) -> None:
    message.message_type = "voice"
    message.transcription_status = status
    message.original_text = original_text

    await test_db.commit()
    await test_db.refresh(message)

    assert message.message_type == "voice"
    assert message.transcription_status == status
    assert message.original_text == original_text


@pytest.mark.parametrize(
    ("message_type", "status", "original_text"),
    [
        ("text", "pending", "Hello"),
        ("voice", None, ""),
        ("voice", "pending", "Voice message"),
        ("voice", "completed", ""),
        ("voice", "completed", "   "),
        ("voice", "failed", "Transcription unavailable"),
        ("audio", None, ""),
        ("voice", "translating", ""),
    ],
)
@pytest.mark.asyncio
async def test_database_rejects_impossible_message_lifecycle_states(
    test_db: AsyncSession,
    message: Message,
    message_type: str,
    status: str | None,
    original_text: str,
) -> None:
    message.message_type = message_type
    message.transcription_status = status
    message.original_text = original_text

    with pytest.raises(IntegrityError):
        await test_db.commit()

    await test_db.rollback()
