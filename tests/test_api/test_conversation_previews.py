"""Tests for the last-message preview on the conversation list (docs/api/contract.md §3.5)."""

from datetime import UTC, datetime, timedelta

import pytest

from src.database.models import Message, TranslationResult
from tests.conftest import auth_headers_for_user


@pytest.mark.asyncio
async def test_conversation_list_carries_the_newest_message_and_its_time(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """The preview is the latest message, not the first one written."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    start = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    test_db.add_all([
        Message(
            client_message_id="preview-old",
            conversation_id=conversation.id,
            sender_id=test_user.id,
            original_text="Tin cũ",
            created_at=start,
        ),
        Message(
            client_message_id="preview-new",
            conversation_id=conversation.id,
            sender_id=test_user_two.id,
            original_text="Tin mới nhất",
            created_at=start + timedelta(minutes=5),
        ),
    ])
    await test_db.commit()

    response = await client.get("/api/v1/conversations", headers=test_user_headers)

    assert response.status_code == 200
    listed = response.json()[0]
    assert listed["last_message"] == "Tin mới nhất"
    assert listed["last_message_at"].startswith("2026-08-14T09:05")


@pytest.mark.asyncio
async def test_preview_uses_the_translation_into_the_readers_language(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Each reader previews the version they can actually read.

    A conversation list showing a language the reader does not speak identifies
    nothing, which is the whole reason this field is per-caller.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = Message(
        client_message_id="preview-translated",
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Deploy xong chưa anh?",
        source_language="vi",
        created_at=datetime.now(UTC),
    )
    test_db.add(message)
    await test_db.commit()

    test_db.add(
        TranslationResult(
            message_id=message.id,
            target_language=test_user.preferred_language,
            translated_text="Is the deploy done?",
            model="llama-3.3-70b-versatile",
            latency_ms=500,
            is_fallback=False,
        )
    )
    await test_db.commit()

    mine = await client.get("/api/v1/conversations", headers=test_user_headers)
    theirs = await client.get(
        "/api/v1/conversations",
        headers=auth_headers_for_user(test_user_two),
    )

    assert mine.json()[0]["last_message"] == "Is the deploy done?"
    # No translation exists into the other member's language, so they keep the
    # original rather than reading someone else's translation.
    assert theirs.json()[0]["last_message"] == "Deploy xong chưa anh?"


@pytest.mark.asyncio
async def test_conversation_without_messages_previews_as_null(
    client,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """A conversation nobody has written in has nothing to preview."""
    await conversation_factory(test_user, [test_user, test_user_two])

    response = await client.get("/api/v1/conversations", headers=test_user_headers)

    listed = response.json()[0]
    assert listed["last_message"] is None
    assert listed["last_message_at"] is None


@pytest.mark.asyncio
async def test_each_conversation_previews_its_own_newest_message(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Ranking happens per conversation, not across the whole message table."""
    first = await conversation_factory(test_user, [test_user, test_user_two])
    second = await conversation_factory(test_user, [test_user, test_user_two])
    start = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    test_db.add_all([
        Message(
            client_message_id="first-latest",
            conversation_id=first.id,
            sender_id=test_user.id,
            original_text="Trong hội thoại một",
            created_at=start,
        ),
        Message(
            client_message_id="second-latest",
            conversation_id=second.id,
            sender_id=test_user.id,
            original_text="Trong hội thoại hai",
            created_at=start + timedelta(hours=1),
        ),
    ])
    await test_db.commit()

    response = await client.get("/api/v1/conversations", headers=test_user_headers)

    previews = {row["id"]: row["last_message"] for row in response.json()}
    assert previews[first.id] == "Trong hội thoại một"
    assert previews[second.id] == "Trong hội thoại hai"


@pytest.mark.asyncio
async def test_timestamps_are_serialised_as_utc_with_a_designator(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Timestamps must carry their zone, as docs/api/contract.md §3.2 has always shown.

    A naive string is parsed by `new Date()` as local time, so a message sent
    seconds ago rendered as hours old for every reader east or west of UTC.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    test_db.add(
        Message(
            client_message_id="tz-check",
            conversation_id=conversation.id,
            sender_id=test_user.id,
            original_text="Kiểm tra múi giờ",
            created_at=datetime(2026, 8, 14, 13, 55, 30, tzinfo=UTC),
        )
    )
    await test_db.commit()

    listed = (await client.get("/api/v1/conversations", headers=test_user_headers)).json()[0]
    history = (
        await client.get(
            f"/api/v1/conversations/{conversation.id}/messages",
            headers=test_user_headers,
        )
    ).json()[0]

    assert listed["last_message_at"] == "2026-08-14T13:55:30Z"
    assert listed["created_at"].endswith("Z")
    assert history["created_at"].endswith("Z")


@pytest.mark.asyncio
async def test_newly_created_conversation_has_no_preview(
    client,
    test_user,
    test_user_headers,
    test_user_two,
):
    """Creation answers with the same shape the list does, previews included."""
    response = await client.post(
        "/api/v1/conversations",
        headers=test_user_headers,
        json={"type": "direct", "member_ids": [test_user_two.id]},
    )

    assert response.status_code == 201
    assert response.json()["last_message"] is None
    assert response.json()["last_message_at"] is None


@pytest.mark.asyncio
async def test_withdrawn_newest_message_previews_the_previous_visible_message(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """A withdrawn newest message falls back to the latest visible message."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    base = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    test_db.add(
        Message(
            client_message_id="preview-older",
            conversation_id=conversation.id,
            sender_id=test_user_two.id,
            original_text="Tin cũ hơn",
            source_language="vi",
            created_at=base,
        )
    )
    test_db.add(
        Message(
            client_message_id="preview-withdrawn",
            conversation_id=conversation.id,
            sender_id=test_user_two.id,
            original_text="Tin bị gỡ",
            source_language="vi",
            created_at=base + timedelta(minutes=5),
            deleted_at=base + timedelta(minutes=6),
        )
    )
    await test_db.commit()

    response = await client.get("/api/v1/conversations", headers=test_user_headers)

    listed = response.json()[0]
    assert listed["last_message"] == "Tin cũ hơn"
    assert listed["last_message_at"].startswith("2026-08-14T09:00")
