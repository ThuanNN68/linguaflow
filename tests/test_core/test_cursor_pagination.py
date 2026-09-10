"""Database and HTTP contracts for composite message cursors."""

from datetime import UTC, datetime, timedelta

import pytest

from src.database.models import Message
from src.services.messaging.chat import (
    ConversationValidationError,
    decode_message_cursor,
    encode_message_cursor,
)


def test_cursor_round_trip_and_validation():
    created_at = datetime(2026, 8, 31, 8, 30, tzinfo=UTC)
    cursor = encode_message_cursor(created_at, "message-id")

    assert decode_message_cursor(cursor) == (created_at, "message-id")
    with pytest.raises(ConversationValidationError, match="Invalid message cursor"):
        decode_message_cursor("not-a-valid-cursor")


@pytest.mark.asyncio
async def test_history_cursor_pages_without_duplicates_at_equal_timestamps(
    client,
    test_db,
    test_user,
    test_user_two,
    test_user_headers,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    start = datetime(2026, 8, 31, 1, 0, tzinfo=UTC)
    messages = [
        Message(
            id=f"00000000-0000-0000-0000-{index:012d}",
            client_message_id=f"cursor-{index}",
            conversation_id=conversation.id,
            sender_id=test_user.id,
            original_text=f"Message {index}",
            created_at=start + timedelta(seconds=min(index, 4)),
        )
        for index in range(1, 6)
    ]
    # The last two share a timestamp, exercising the id half of the cursor.
    messages[-1].created_at = messages[-2].created_at
    test_db.add_all(messages)
    await test_db.commit()

    seen: list[str] = []
    before: str | None = None
    while True:
        params = {"limit": 2, "paginated": "true"}
        if before:
            params["before"] = before
        response = await client.get(
            f"/api/v1/conversations/{conversation.id}/messages",
            headers=test_user_headers,
            params=params,
        )
        assert response.status_code == 200
        page = response.json()
        seen.extend(item["id"] for item in page["items"])
        before = page["next_cursor"]
        if not page["has_more"]:
            break

    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == {message.id for message in messages}


@pytest.mark.asyncio
async def test_history_rejects_bad_cursor_and_supports_offset_fallback(
    client,
    test_db,
    test_user,
    test_user_two,
    test_user_headers,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    test_db.add_all(
        Message(
            client_message_id=f"offset-{index}",
            conversation_id=conversation.id,
            sender_id=test_user.id,
            original_text=str(index),
            created_at=datetime(2026, 8, 31, 2, index, tzinfo=UTC),
        )
        for index in range(4)
    )
    await test_db.commit()

    invalid = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
        params={"before": "broken"},
    )
    assert invalid.status_code == 400

    offset_page = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
        params={"paginated": "true", "limit": 2, "offset": 2},
    )
    assert offset_page.status_code == 200
    assert [item["client_message_id"] for item in offset_page.json()["items"]] == [
        "offset-0",
        "offset-1",
    ]
