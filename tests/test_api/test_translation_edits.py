"""Tests for the translation edit endpoint (F-05, docs/api/contract.md §3.10)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.database.models import Message, TranslationEdit, TranslationResult
from tests.conftest import auth_headers_for_user


async def _seed_translation(
    test_db,
    conversation_id: str,
    sender_id: str,
    *,
    client_message_id: str = "edit-seed",
) -> tuple[str, str]:
    """Persist one message with one translation.

    Returns:
        The message id and the translation id.
    """
    message = Message(
        client_message_id=client_message_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text="Chiều nay họp lúc mấy giờ?",
        source_language="vi",
        created_at=datetime.now(UTC),
    )
    test_db.add(message)
    await test_db.commit()

    translation = TranslationResult(
        message_id=message.id,
        target_language="en",
        translated_text="What time is the meeting this afternoon?",
        model="llama-3.3-70b-versatile",
        latency_ms=512,
        is_fallback=False,
    )
    test_db.add(translation)
    await test_db.commit()
    return message.id, translation.id


@pytest.mark.asyncio
async def test_edit_from_member_is_stored_without_touching_the_machine_text(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """An edit is stored beside the LLM's wording, never over it (ADR-19)."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    _, translation_id = await _seed_translation(
        test_db, conversation.id, test_user_two.id
    )

    response = await client.post(
        f"/api/v1/translations/{translation_id}/edits",
        headers=test_user_headers,
        json={"edited_text": "When is this afternoon's meeting?"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["translation_id"] == translation_id
    assert body["target_language"] == "en"
    assert body["edited_text"] == "When is this afternoon's meeting?"

    stored = await test_db.scalar(
        select(TranslationEdit).where(TranslationEdit.id == body["edit_id"])
    )
    assert stored is not None
    assert stored.editor_id == test_user.id

    translation = await test_db.get(TranslationResult, translation_id)
    await test_db.refresh(translation)
    assert translation.translated_text == "What time is the meeting this afternoon?"


@pytest.mark.asyncio
async def test_editing_twice_keeps_both_rows_and_reports_the_newer_one(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """The table is append-only, unlike feedback where a second vote replaces."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    _, translation_id = await _seed_translation(
        test_db, conversation.id, test_user_two.id
    )

    for text in ("First try", "Second try"):
        response = await client.post(
            f"/api/v1/translations/{translation_id}/edits",
            headers=test_user_headers,
            json={"edited_text": text},
        )
        assert response.status_code == 201

    rows = list(
        (
            await test_db.scalars(
                select(TranslationEdit).where(
                    TranslationEdit.translation_id == translation_id
                )
            )
        ).all()
    )
    assert len(rows) == 2
    assert {row.edited_text for row in rows} == {"First try", "Second try"}

    history = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )
    assert history.status_code == 200
    translations = history.json()[0]["translations"]
    assert translations[0]["my_edit"]["edited_text"] == "Second try"


@pytest.mark.asyncio
async def test_history_hides_an_edit_written_by_another_account(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """`my_edit` is per-caller: one member never reads another member's wording."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    _, translation_id = await _seed_translation(
        test_db, conversation.id, test_user_two.id
    )

    written = await client.post(
        f"/api/v1/translations/{translation_id}/edits",
        headers=auth_headers_for_user(test_user_two),
        json={"edited_text": "Only the other member should see this"},
    )
    assert written.status_code == 201

    history = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )

    assert history.status_code == 200
    translations = history.json()[0]["translations"]
    assert translations[0]["my_edit"] is None


@pytest.mark.asyncio
async def test_edit_from_outsider_is_refused(
    client,
    test_db,
    test_user,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    """Holding a translation id is not membership of the conversation."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    _, translation_id = await _seed_translation(
        test_db, conversation.id, test_user_two.id
    )

    response = await client.post(
        f"/api/v1/translations/{translation_id}/edits",
        headers=auth_headers_for_user(test_user_three),
        json={"edited_text": "Not mine to edit"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_edit_of_a_withdrawn_message_is_refused(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """A withdrawn message has no wording left to argue about (§3.6)."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message_id, translation_id = await _seed_translation(
        test_db, conversation.id, test_user_two.id
    )
    message = await test_db.get(Message, message_id)
    message.deleted_at = datetime.now(UTC)
    await test_db.commit()

    response = await client.post(
        f"/api/v1/translations/{translation_id}/edits",
        headers=test_user_headers,
        json={"edited_text": "Too late"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_edit_of_an_unknown_translation_is_not_found(
    client,
    test_user_headers,
):
    """An id that matches nothing is a 404, not a 403 that leaks its absence."""
    response = await client.post(
        "/api/v1/translations/does-not-exist/edits",
        headers=test_user_headers,
        json={"edited_text": "Nothing to edit"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_blank_edit_text_is_rejected(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Whitespace would read as "cleared" but store as set."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    _, translation_id = await _seed_translation(
        test_db, conversation.id, test_user_two.id
    )

    response = await client.post(
        f"/api/v1/translations/{translation_id}/edits",
        headers=test_user_headers,
        json={"edited_text": "   "},
    )

    assert response.status_code == 422
