"""Saved messages API tests."""

from datetime import UTC, datetime

import pytest

from src.database.models import Message


@pytest.mark.asyncio
async def test_list_saved_messages_empty_initially(client, test_user, test_user_headers):
    response = await client.get("/api/v1/saved-messages", headers=test_user_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["has_more"] is False


@pytest.mark.asyncio
async def test_save_unsave_and_list_saved_messages(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    msg1 = Message(
        client_message_id=f"msg1-{datetime.now(UTC).timestamp()}",
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Message number 1",
        source_language="en",
        created_at=datetime.now(UTC),
    )
    msg2 = Message(
        client_message_id=f"msg2-{datetime.now(UTC).timestamp()}",
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Message number 2",
        source_language="en",
        created_at=datetime.now(UTC),
    )
    test_db.add_all([msg1, msg2])
    await test_db.commit()

    # Save msg1
    res = await client.put(
        f"/api/v1/conversations/{conversation.id}/messages/{msg1.id}/saved",
        headers=test_user_headers,
    )
    assert res.status_code == 200
    assert res.json() == {"message_id": msg1.id, "is_saved": True}

    # Save msg2
    res = await client.put(
        f"/api/v1/conversations/{conversation.id}/messages/{msg2.id}/saved",
        headers=test_user_headers,
    )
    assert res.status_code == 200
    assert res.json() == {"message_id": msg2.id, "is_saved": True}

    # List saved messages
    list_res = await client.get("/api/v1/saved-messages", headers=test_user_headers)
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    assert len(items) == 2
    assert items[0]["id"] == msg2.id
    assert items[0]["is_saved"] is True
    assert items[1]["id"] == msg1.id
    assert items[1]["is_saved"] is True

    # Unsave msg1
    del_res = await client.delete(
        f"/api/v1/conversations/{conversation.id}/messages/{msg1.id}/saved",
        headers=test_user_headers,
    )
    assert del_res.status_code == 200
    assert del_res.json() == {"message_id": msg1.id, "is_saved": False}

    # Verify only msg2 is saved now
    list_res2 = await client.get("/api/v1/saved-messages", headers=test_user_headers)
    assert list_res2.status_code == 200
    items2 = list_res2.json()["items"]
    assert len(items2) == 1
    assert items2[0]["id"] == msg2.id


@pytest.mark.asyncio
async def test_saved_messages_unauthorized_after_leaving_group(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    """User saves message in group, leaves group, message is not returned in saved list."""
    # User two is creator/owner, test_user and test_user_three are members
    group = await conversation_factory(
        test_user_two,
        [test_user_two, test_user, test_user_three],
        conversation_type="group",
        title="Engineering Team",
    )
    msg = Message(
        client_message_id=f"group-msg-{datetime.now(UTC).timestamp()}",
        conversation_id=group.id,
        sender_id=test_user_two.id,
        original_text="Confidential roadmap announcement",
        source_language="en",
        created_at=datetime.now(UTC),
    )
    test_db.add(msg)
    await test_db.commit()

    # User saves message while still a member
    save_res = await client.put(
        f"/api/v1/conversations/{group.id}/messages/{msg.id}/saved",
        headers=test_user_headers,
    )
    assert save_res.status_code == 200
    assert save_res.json()["is_saved"] is True

    # Confirm message is in saved list
    before_leave = await client.get("/api/v1/saved-messages", headers=test_user_headers)
    assert before_leave.status_code == 200
    assert len(before_leave.json()["items"]) == 1
    assert before_leave.json()["items"][0]["id"] == msg.id

    # User leaves group
    leave_res = await client.post(
        f"/api/v1/conversations/{group.id}/leave",
        headers=test_user_headers,
    )
    assert leave_res.status_code == 204

    # GET saved-messages must NOT return the message anymore
    after_leave = await client.get("/api/v1/saved-messages", headers=test_user_headers)
    assert after_leave.status_code == 200
    assert after_leave.json()["items"] == []

    # Verify that if user has another authorized direct message saved, it is still returned
    dm = await conversation_factory(test_user, [test_user, test_user_two])
    dm_msg = Message(
        client_message_id=f"dm-msg-{datetime.now(UTC).timestamp()}",
        conversation_id=dm.id,
        sender_id=test_user_two.id,
        original_text="Direct memo",
        source_language="en",
        created_at=datetime.now(UTC),
    )
    test_db.add(dm_msg)
    await test_db.commit()

    dm_save = await client.put(
        f"/api/v1/conversations/{dm.id}/messages/{dm_msg.id}/saved",
        headers=test_user_headers,
    )
    assert dm_save.status_code == 200

    final_list = await client.get("/api/v1/saved-messages", headers=test_user_headers)
    assert final_list.status_code == 200
    final_items = final_list.json()["items"]
    assert len(final_items) == 1
    assert final_items[0]["id"] == dm_msg.id
