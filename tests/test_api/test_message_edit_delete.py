"""Tests for editing and withdrawing messages (F-06, docs/api/contract.md §3.6)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.database.models import Message, TranslationResult
from tests.conftest import auth_headers_for_user


async def _seed_message(test_db, conversation_id: str, sender_id: str, *, translated=True):
    """Persist one message, optionally with a translation attached to it."""
    message = Message(
        client_message_id=f"edit-seed-{sender_id[:8]}",
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text="Chiều nay họp lúc 3 giờ",
        source_language="vi",
        created_at=datetime.now(UTC),
    )
    test_db.add(message)
    await test_db.commit()

    if translated:
        test_db.add(
            TranslationResult(
                message_id=message.id,
                target_language="en",
                translated_text="The meeting is at 3pm today",
                model="llama-3.3-70b-versatile",
                latency_ms=500,
                is_fallback=False,
            )
        )
        await test_db.commit()
    return message


@pytest.mark.asyncio
async def test_sender_can_edit_their_message_and_stale_translation_is_dropped(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Editing replaces the text and discards translations of the old text.

    Keeping them would leave other members reading a translation of a sentence
    the sender has already retracted.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(test_db, conversation.id, test_user.id)

    response = await client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=test_user_headers,
        json={"text": "Chiều nay họp lúc 4 giờ nhé"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["original_text"] == "Chiều nay họp lúc 4 giờ nhé"
    assert body["edited_at"] is not None
    assert body["edited_at"].endswith("Z")
    assert body["translations"] == []

    remaining = (
        await test_db.scalars(
            select(TranslationResult).where(TranslationResult.message_id == message.id)
        )
    ).all()
    assert remaining == []


@pytest.mark.asyncio
async def test_other_members_cannot_edit_someone_elses_message(
    client,
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    """Membership is not authorship: only the sender may rewrite their words."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(test_db, conversation.id, test_user.id)

    response = await client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=auth_headers_for_user(test_user_two),
        json={"text": "Tôi sửa lời của người khác"},
    )

    assert response.status_code == 403
    await test_db.refresh(message)
    assert message.original_text == "Chiều nay họp lúc 3 giờ"
    assert message.edited_at is None


@pytest.mark.asyncio
async def test_deleting_keeps_the_row_and_blanks_the_text_everywhere(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Withdrawal is soft: the row survives, the words do not.

    The row has to stay because `translation_results` and `translation_attempts`
    reference it, and ADR-16 keeps those as measurement evidence.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(test_db, conversation.id, test_user.id)

    response = await client.delete(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=test_user_headers,
    )

    assert response.status_code == 204

    stored = await test_db.get(Message, message.id)
    assert stored is not None
    # The session cached this row when the test seeded it, so it has to be
    # re-read before it can show what the endpoint wrote.
    await test_db.refresh(stored)
    assert stored.deleted_at is not None

    history = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=auth_headers_for_user(test_user_two),
    )
    row = history.json()[0]
    assert row["original_text"] == ""
    assert row["translations"] == []
    assert row["deleted_at"].endswith("Z")


@pytest.mark.asyncio
async def test_editing_a_withdrawn_message_is_refused(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """A message the sender already withdrew cannot be brought back by editing."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(test_db, conversation.id, test_user.id)

    await client.delete(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=test_user_headers,
    )
    response = await client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=test_user_headers,
        json={"text": "Quay lại nào"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_deleting_twice_is_not_an_error(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """The caller asked for the message to be gone, and it is."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(test_db, conversation.id, test_user.id)

    first = await client.delete(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=test_user_headers,
    )
    stored = await test_db.get(Message, message.id)
    await test_db.refresh(stored)
    deleted_at = stored.deleted_at

    second = await client.delete(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=test_user_headers,
    )

    assert first.status_code == 204
    assert second.status_code == 204
    await test_db.refresh(stored)
    # The first withdrawal time stands; a repeat must not move it.
    assert stored.deleted_at == deleted_at


@pytest.mark.asyncio
async def test_message_from_another_conversation_reads_as_missing(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """A mismatched conversation id is a 404, never a 403 that confirms existence."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    other = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(test_db, conversation.id, test_user.id)

    response = await client.delete(
        f"/api/v1/conversations/{other.id}/messages/{message.id}",
        headers=test_user_headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_non_member_cannot_touch_messages(
    client,
    test_db,
    test_user,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    """Someone outside the conversation is refused before ownership is considered."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(test_db, conversation.id, test_user.id)

    response = await client.delete(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=auth_headers_for_user(test_user_three),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_blank_edit_is_rejected(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Blanking a message is what DELETE is for, and it records the withdrawal."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(test_db, conversation.id, test_user.id)

    response = await client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        headers=test_user_headers,
        json={"text": "   "},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_withdrawn_message_previews_the_previous_visible_message(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """The sidebar shows the newest message that remains visible."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    earlier = Message(
        client_message_id="preview-earlier",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="Tin trước đó",
        created_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
    )
    latest = Message(
        client_message_id="preview-latest",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="Tin sẽ bị gỡ",
        created_at=datetime(2026, 8, 14, 9, 5, tzinfo=UTC),
    )
    test_db.add_all([earlier, latest])
    await test_db.commit()

    await client.delete(
        f"/api/v1/conversations/{conversation.id}/messages/{latest.id}",
        headers=test_user_headers,
    )

    listed = (await client.get("/api/v1/conversations", headers=test_user_headers)).json()[0]
    assert listed["last_message"] == "Tin trước đó"
    assert listed["last_message_at"].startswith("2026-08-14T09:00")
