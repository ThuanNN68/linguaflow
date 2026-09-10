"""Tests that a private assistant reply reaches nobody but the person who asked.

`docs/architecture/assistant-admin.md` §3.3 calls this the easiest place in the design to leak
from, and names the three ways in: reloading history, searching the conversation,
and trusting the frontend to hide what the API already sent. Each has a test
here, and the third is asserted against the compiled SQL rather than the response
body — a filter applied after the rows are fetched passes a body check and still
puts the text on the wire.

Nothing here mocks an LLM: the reply text is written directly, because what is
under test is who can read a row, not what the model put in it.

Fixtures live in this file rather than tests/conftest.py, which is shared across
all feature areas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Conversation, Message
from src.services.messaging.chat import ChatService
from src.services.messaging.message_visibility import public_only, visible_to
from tests.conftest import auth_headers_for_user


@pytest_asyncio.fixture
async def group_with_private_reply(
    test_db: AsyncSession, test_user, test_user_two, test_user_three, conversation_factory
) -> dict:
    """A three-person group holding one public message and one private reply."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two, test_user_three], conversation_type="group"
    )
    # Explicit timestamps, and this is not decoration. PostgreSQL's `now()` is
    # the *transaction* start time, so two rows inserted in one transaction take
    # the identical `created_at` and the preview's `ORDER BY created_at DESC,
    # id DESC` falls through to a random uuid — a coin flip deciding which
    # message is "newest". Stating the order in the data is what makes the
    # preview assertions below mean anything.
    base = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    public = Message(
        client_message_id=f"m-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="Deadline nộp báo cáo là thứ sáu",
        source_language="vi",
        created_at=base,
    )
    test_db.add(public)
    await test_db.flush()

    private = Message(
        client_message_id=f"assistant:{public.id}",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="Bí mật: Khánh Thi hứa xong phần backend trước thứ năm",
        source_language="vi",
        assistant_generated=True,
        reply_to_message_id=public.id,
        visibility="private",
        visible_to_user_id=test_user.id,
        created_at=base + timedelta(minutes=1),
    )
    test_db.add(private)
    await test_db.commit()

    return {
        "conversation": conversation,
        "public": public,
        "private": private,
        "owner": test_user,
        "other": test_user_two,
    }


@pytest.mark.asyncio
async def test_other_member_reloading_history_never_sees_the_assistant_reply(
    client: AsyncClient, group_with_private_reply
) -> None:
    """The first of the three leaks §3.3 names."""
    conversation = group_with_private_reply["conversation"]
    other = group_with_private_reply["other"]
    private = group_with_private_reply["private"]

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=auth_headers_for_user(other),
    )

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert private.id not in ids
    assert group_with_private_reply["public"].id in ids


@pytest.mark.asyncio
async def test_the_person_who_asked_still_sees_their_own_assistant_reply(
    client: AsyncClient, group_with_private_reply
) -> None:
    """Hiding it from everyone would be a different bug with the same test."""
    conversation = group_with_private_reply["conversation"]
    owner = group_with_private_reply["owner"]
    private = group_with_private_reply["private"]

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=auth_headers_for_user(owner),
    )

    assert response.status_code == 200
    assert private.id in [item["id"] for item in response.json()]


@pytest.mark.asyncio
async def test_conversation_search_cannot_surface_another_members_private_message(
    client: AsyncClient, group_with_private_reply
) -> None:
    """The second leak: search is a second way into the same table."""
    conversation = group_with_private_reply["conversation"]
    other = group_with_private_reply["other"]
    private = group_with_private_reply["private"]

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=auth_headers_for_user(other),
        params={"q": "Khánh Thi"},
    )

    assert response.status_code == 200
    body = response.json()
    assert private.id not in [item["message"]["id"] for item in body["items"]]


@pytest.mark.asyncio
async def test_private_messages_are_excluded_in_sql_not_after_fetching(
    test_db: AsyncSession, test_user
) -> None:
    """The third leak, and the reason this test reads SQL instead of a response.

    A body assertion passes just as happily when the rows were selected and then
    dropped in Python. That version still sends the text to the application, and
    the same mistake one layer out — filtering in the browser — is what §3.3
    calls the classic security bug.
    """
    compiled = str(visible_to(test_user.id).compile(compile_kwargs={"literal_binds": True}))

    assert "messages.visibility" in compiled
    assert "messages.visible_to_user_id" in compiled
    assert " OR " in compiled.upper()


@pytest.mark.asyncio
async def test_an_unread_badge_does_not_move_for_a_message_you_cannot_open(
    test_db: AsyncSession, group_with_private_reply
) -> None:
    """A count that moves is itself a disclosure that something was said."""
    conversation = group_with_private_reply["conversation"]
    other = group_with_private_reply["other"]

    counts = await ChatService(test_db).get_unread_counts(
        user_id=other.id, conversation_ids=[conversation.id]
    )

    # Only the public message counts; the private reply is not readable and so
    # is not unread either.
    assert counts.get(conversation.id, 0) == 1


@pytest.mark.asyncio
async def test_another_member_cannot_reach_a_private_message_by_id(
    test_db: AsyncSession, group_with_private_reply
) -> None:
    """The gate that edit, delete and reactions all share.

    Reported as not found rather than forbidden: the caller supplied the id, so
    "it exists but is not yours" would be the only new fact in the answer.
    """
    from src.services.messaging.chat import MessageNotFoundError

    conversation = group_with_private_reply["conversation"]
    other = group_with_private_reply["other"]
    private = group_with_private_reply["private"]

    with pytest.raises(MessageNotFoundError):
        await ChatService(test_db).get_message_for_member(
            user_id=other.id,
            conversation_id=conversation.id,
            message_id=private.id,
        )


@pytest.mark.asyncio
async def test_shared_translation_context_excludes_every_private_message(
    test_db: AsyncSession, test_user
) -> None:
    """A translation is built once and delivered to everyone who reads it.

    So its context takes `public_only`, not the reader's own view: a message
    kept from some members must not shape wording all of them are shown.
    """
    compiled = str(public_only().compile(compile_kwargs={"literal_binds": True}))

    assert "messages.visibility" in compiled
    assert "visible_to_user_id" not in compiled


@pytest.mark.asyncio
async def test_assistant_reply_is_addressed_only_to_the_member_who_tagged_it(
    test_db: AsyncSession, group_with_private_reply, monkeypatch
) -> None:
    """The recipient list is the fan-out; getting it wrong delivers to the group."""
    public = group_with_private_reply["public"]
    owner = group_with_private_reply["owner"]

    async def canned(self, trigger_message):
        return "Tóm tắt riêng cho bạn"

    monkeypatch.setattr(ChatService, "_assistant_reply_text", canned)

    # A different trigger message, so the fixture's reply does not satisfy the
    # idempotency lookup and short-circuit what is under test.
    trigger = Message(
        client_message_id=f"m-{uuid.uuid4().hex[:8]}",
        conversation_id=public.conversation_id,
        sender_id=owner.id,
        original_text="@assistant tóm tắt giúp mình",
        source_language="vi",
    )
    test_db.add(trigger)
    await test_db.commit()

    result = await ChatService(test_db).create_assistant_reply(trigger_message=trigger)

    assert result.recipient_ids == (owner.id,)
    assert result.message.visibility == "private"
    assert result.message.visible_to_user_id == owner.id


@pytest.mark.asyncio
async def test_a_conversation_preview_falls_back_to_the_newest_readable_message(
    test_db: AsyncSession, group_with_private_reply
) -> None:
    """Ranking must happen inside the filter, not on top of its result.

    Rank over everything and the private reply wins, gets discarded, and the
    conversation shows no preview at all rather than the message this member can
    actually see.
    """
    conversation = group_with_private_reply["conversation"]
    other = group_with_private_reply["other"]

    previews = await ChatService(test_db).get_last_messages(
        conversation_ids=[conversation.id],
        reader_language="vi",
        reader_id=other.id,
    )

    assert conversation.id in previews
    assert previews[conversation.id][0] == "Deadline nộp báo cáo là thứ sáu"


@pytest.mark.asyncio
async def test_the_owner_preview_shows_their_private_reply(
    test_db: AsyncSession, group_with_private_reply
) -> None:
    """The same ranking, for the one member the reply belongs to."""
    conversation: Conversation = group_with_private_reply["conversation"]
    owner = group_with_private_reply["owner"]

    previews = await ChatService(test_db).get_last_messages(
        conversation_ids=[conversation.id],
        reader_language="vi",
        reader_id=owner.id,
    )

    assert "Khánh Thi" in previews[conversation.id][0]
