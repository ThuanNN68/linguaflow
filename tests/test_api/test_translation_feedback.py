"""Tests for the translation feedback endpoint (F-05)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.database.models import Feedback, Message, TranslationResult
from tests.conftest import auth_headers_for_user


async def _seed_translation(test_db, conversation_id: str, sender_id: str) -> str:
    """Persist one message with one translation and return the translation id."""
    message = Message(
        client_message_id="feedback-seed",
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text="Deploy xong chưa anh?",
        source_language="vi",
        created_at=datetime.now(UTC),
    )
    test_db.add(message)
    await test_db.commit()

    translation = TranslationResult(
        message_id=message.id,
        target_language="en",
        translated_text="Is the deploy done?",
        model="llama-3.3-70b-versatile",
        latency_ms=640,
        is_fallback=False,
    )
    test_db.add(translation)
    await test_db.commit()
    return translation.id


@pytest.mark.asyncio
async def test_feedback_from_member_is_stored_with_rating_and_correction(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """A reader in the conversation can rate a translation and suggest better text."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    translation_id = await _seed_translation(test_db, conversation.id, test_user_two.id)

    response = await client.post(
        f"/api/v1/translations/{translation_id}/feedback",
        headers=test_user_headers,
        json={"rating": 5, "correction": "Has the deploy finished?"},
    )

    assert response.status_code == 201
    feedback_id = response.json()["feedback_id"]

    stored = await test_db.scalar(select(Feedback).where(Feedback.id == feedback_id))
    assert stored is not None
    assert stored.translation_id == translation_id
    assert stored.user_id == test_user.id
    assert stored.rating == 5
    assert stored.correction == "Has the deploy finished?"


@pytest.mark.asyncio
async def test_second_vote_from_same_reader_replaces_the_first(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Changing a vote overwrites it: one reader leaves at most one row.

    The table carries no unique constraint on (translation_id, user_id), so
    nothing but the service prevents a reader's every click from accumulating.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    translation_id = await _seed_translation(test_db, conversation.id, test_user_two.id)

    thumbs_up = await client.post(
        f"/api/v1/translations/{translation_id}/feedback",
        headers=test_user_headers,
        json={"rating": 5, "correction": None},
    )
    thumbs_down = await client.post(
        f"/api/v1/translations/{translation_id}/feedback",
        headers=test_user_headers,
        json={"rating": 1, "correction": None},
    )

    assert thumbs_up.status_code == 201
    assert thumbs_down.status_code == 201
    assert thumbs_up.json()["feedback_id"] == thumbs_down.json()["feedback_id"]

    rows = (
        await test_db.scalars(
            select(Feedback).where(
                Feedback.translation_id == translation_id,
                Feedback.user_id == test_user.id,
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].rating == 1


@pytest.mark.asyncio
async def test_feedback_from_non_member_is_rejected(
    client,
    test_db,
    test_user,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    """Holding a translation id is not authorization to rate it."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    translation_id = await _seed_translation(test_db, conversation.id, test_user_two.id)

    response = await client.post(
        f"/api/v1/translations/{translation_id}/feedback",
        headers=auth_headers_for_user(test_user_three),
        json={"rating": 1, "correction": None},
    )

    assert response.status_code == 403
    assert await test_db.scalar(select(Feedback).limit(1)) is None


@pytest.mark.asyncio
async def test_feedback_on_unknown_translation_returns_not_found(
    client,
    test_user_headers,
):
    """An id that matches no translation is a 404, not a silent insert."""
    response = await client.post(
        "/api/v1/translations/00000000-0000-0000-0000-000000000000/feedback",
        headers=test_user_headers,
        json={"rating": 5, "correction": None},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rating_outside_the_allowed_range_is_rejected(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """The request is refused before it can violate the table's check constraint."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    translation_id = await _seed_translation(test_db, conversation.id, test_user_two.id)

    response = await client.post(
        f"/api/v1/translations/{translation_id}/feedback",
        headers=test_user_headers,
        json={"rating": 9, "correction": None},
    )

    assert response.status_code == 422
    assert await test_db.scalar(select(Feedback).limit(1)) is None


@pytest.mark.asyncio
async def test_history_returns_only_the_calling_readers_own_feedback(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """History carries my vote back so a reload cannot lose it — and only mine.

    Without this the rating button forgets its state on every reload, and the
    user cannot tell "not voted" from "voted, forgotten".
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    translation_id = await _seed_translation(test_db, conversation.id, test_user_two.id)

    await client.post(
        f"/api/v1/translations/{translation_id}/feedback",
        headers=test_user_headers,
        json={"rating": 1, "correction": "Đã deploy xong chưa?"},
    )

    mine = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )
    theirs = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=auth_headers_for_user(test_user_two),
    )

    my_translation = mine.json()[0]["translations"][0]
    assert my_translation["my_rating"] == 1
    assert my_translation["my_correction"] == "Đã deploy xong chưa?"

    their_translation = theirs.json()[0]["translations"][0]
    assert their_translation["my_rating"] is None
    assert their_translation["my_correction"] is None


@pytest.mark.asyncio
async def test_blank_correction_is_stored_as_null(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Whitespace is not a correction, so it must not read as one downstream."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    translation_id = await _seed_translation(test_db, conversation.id, test_user_two.id)

    response = await client.post(
        f"/api/v1/translations/{translation_id}/feedback",
        headers=test_user_headers,
        json={"rating": 5, "correction": "   "},
    )

    assert response.status_code == 201
    stored = await test_db.scalar(
        select(Feedback).where(Feedback.id == response.json()["feedback_id"])
    )
    assert stored is not None
    assert stored.correction is None
