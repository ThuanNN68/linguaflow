"""Tests for the minimal authenticated conversation REST API."""

from datetime import UTC, datetime, timedelta

import pytest

from src.database.models import Message, TranslationResult
from tests.conftest import auth_headers_for_user


@pytest.mark.asyncio
async def test_create_direct_conversation_adds_creator_once(
    client,
    test_user,
    test_user_headers,
    test_user_two,
):
    """The creator is automatically included and duplicate IDs are harmless."""
    response = await client.post(
        "/api/v1/conversations",
        headers=test_user_headers,
        json={
            "type": "direct",
            "member_ids": [test_user_two.id, test_user.id, test_user_two.id],
            "title": "Project chat",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["type"] == "direct"
    assert data["title"] == "Project chat"
    assert data["created_by"] == test_user.id
    assert set(data["member_ids"]) == {test_user.id, test_user_two.id}
    assert len(data["member_ids"]) == 2


@pytest.mark.asyncio
async def test_creating_the_same_direct_conversation_twice_reuses_the_first(
    client,
    test_user,
    test_user_headers,
    test_user_two,
):
    """A second thread between the same two people would split their history."""
    body = {"type": "direct", "member_ids": [test_user_two.id]}

    first = await client.post("/api/v1/conversations", headers=test_user_headers, json=body)
    second = await client.post("/api/v1/conversations", headers=test_user_headers, json=body)

    assert first.status_code == 201
    # 200 rather than 201, so the client can tell it did not create anything.
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


@pytest.mark.asyncio
async def test_the_other_member_reopening_a_direct_conversation_lands_in_the_same_one(
    client,
    test_user,
    test_user_headers,
    test_user_two,
):
    """Whoever writes first owns the thread; the other must not start a rival one."""
    first = await client.post(
        "/api/v1/conversations",
        headers=test_user_headers,
        json={"type": "direct", "member_ids": [test_user_two.id]},
    )
    second = await client.post(
        "/api/v1/conversations",
        headers=auth_headers_for_user(test_user_two),
        json={"type": "direct", "member_ids": [test_user.id]},
    )

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


@pytest.mark.asyncio
async def test_two_groups_with_the_same_members_stay_separate(
    client,
    test_user_headers,
    test_user_two,
):
    """Groups are told apart by purpose, not by who is in them."""
    body = {"type": "group", "member_ids": [test_user_two.id], "title": "Nhóm dự án"}

    first = await client.post("/api/v1/conversations", headers=test_user_headers, json=body)
    second = await client.post("/api/v1/conversations", headers=test_user_headers, json=body)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["id"] != second.json()["id"]


@pytest.mark.asyncio
async def test_conversation_members_carry_a_name_to_display(
    client,
    test_user_headers,
    test_user_two,
):
    """Without it a client can only cut the email at the `@` (CONTRACT §3.5)."""
    response = await client.post(
        "/api/v1/conversations",
        headers=test_user_headers,
        json={"type": "direct", "member_ids": [test_user_two.id]},
    )

    members = {member["id"]: member for member in response.json()["members"]}
    assert members[test_user_two.id]["display_name"] == test_user_two.email.split("@")[0]
    assert members[test_user_two.id]["username"] == test_user_two.email.split("@")[0]


@pytest.mark.asyncio
async def test_create_conversation_validates_member_rules(
    client,
    test_user,
    test_user_headers,
):
    """Direct/group cardinality and referenced users are checked server-side."""
    direct_response = await client.post(
        "/api/v1/conversations",
        headers=test_user_headers,
        json={"type": "direct", "member_ids": []},
    )
    assert direct_response.status_code == 400

    group_response = await client.post(
        "/api/v1/conversations",
        headers=test_user_headers,
        json={"type": "group", "member_ids": []},
    )
    assert group_response.status_code == 400

    missing_user_response = await client.post(
        "/api/v1/conversations",
        headers=test_user_headers,
        json={"type": "direct", "member_ids": ["missing-user-id"]},
    )
    assert missing_user_response.status_code == 400


@pytest.mark.asyncio
async def test_list_conversations_only_returns_memberships(
    client,
    conversation_factory,
    test_user,
    test_user_headers,
    test_user_two,
    test_user_three,
):
    """A user cannot discover conversations they do not belong to."""
    visible = await conversation_factory(test_user, [test_user_two])
    await conversation_factory(test_user_two, [test_user_three])

    response = await client.get("/api/v1/conversations", headers=test_user_headers)

    assert response.status_code == 200
    assert [conversation["id"] for conversation in response.json()] == [visible.id]


@pytest.mark.asyncio
async def test_conversation_history_is_member_protected(
    client,
    conversation_factory,
    test_user,
    test_user_two,
    test_user_three,
):
    """A non-member receives a forbidden response instead of message history."""
    conversation = await conversation_factory(test_user, [test_user_two])

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=auth_headers_for_user(test_user_three),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You are not a member of this conversation"


@pytest.mark.asyncio
async def test_conversation_history_returns_recent_messages_chronologically(
    client,
    conversation_factory,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
):
    """Recent history has a stable chronological order and includes client IDs."""
    conversation = await conversation_factory(test_user, [test_user_two])
    first_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    messages = [
        Message(
            client_message_id="history-1",
            conversation_id=conversation.id,
            sender_id=test_user.id,
            original_text="First",
            created_at=first_time,
        ),
        Message(
            client_message_id="history-2",
            conversation_id=conversation.id,
            sender_id=test_user_two.id,
            original_text="Second",
            created_at=first_time + timedelta(seconds=1),
        ),
        Message(
            client_message_id="history-3",
            conversation_id=conversation.id,
            sender_id=test_user.id,
            original_text="Third",
            created_at=first_time + timedelta(seconds=2),
        ),
    ]
    test_db.add_all(messages)
    await test_db.commit()

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages?limit=2",
        headers=test_user_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert [message["client_message_id"] for message in data] == ["history-2", "history-3"]
    assert [message["original_text"] for message in data] == ["Second", "Third"]

    invalid_limit = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages?limit=101",
        headers=test_user_headers,
    )
    assert invalid_limit.status_code == 422


@pytest.mark.asyncio
async def test_conversation_response_carries_member_profiles(
    client, test_user, test_user_two, test_user_headers, conversation_factory
):
    """`members` must be populated, not just `member_ids`.

    The id list alone leaves a direct conversation with no name to show and
    every incoming message unattributed, and a group header counting "0 thành
    viên". The field defaults to an empty list, so forgetting to fill it fails
    silently in the API and only surfaces as wrong text on screen.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])

    response = await client.get("/api/v1/conversations", headers=test_user_headers)

    assert response.status_code == 200
    listed = next(c for c in response.json() if c["id"] == conversation.id)
    assert sorted(m["id"] for m in listed["members"]) == sorted(listed["member_ids"])
    assert all(m["preferred_language"] for m in listed["members"])


@pytest.mark.asyncio
async def test_history_returns_translations_with_their_id(
    client, test_db, test_user, test_user_two, test_user_headers, conversation_factory
):
    """A message's translations come back, keyed under `translation_id`.

    The row's primary key is `id`; the client needs it as `translation_id` so a
    correction can be attached to a translation (docs/api/contract.md section 4.4).
    Validating the ORM object straight into the schema cannot rename a field, so
    it raised and every history request answered 500 — with no test covering a
    message that actually had a translation, nothing caught it.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = Message(
        client_message_id="with-translation",
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
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

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )

    assert response.status_code == 200
    row = next(item for item in response.json() if item["client_message_id"] == "with-translation")
    assert [t["translation_id"] for t in row["translations"]] == [translation.id]
    assert row["translations"][0]["translated_text"] == "Is the deploy done?"
