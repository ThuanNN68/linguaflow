"""Tests for which translation a reader is served once standings exist.

Nothing in the product creates a second standing yet — the fan-out that does is
a later change — so these seed the rows directly. That is the point: the read
path has to be correct *before* anything starts writing several buckets,
because the failure mode is silent. Two rows for one language and a reader who
picks arbitrarily produces a perfectly ordinary-looking message in the wrong
register, with no error anywhere.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Message, ParticipantProfile, TranslationResult
from tests.conftest import auth_headers_for_user


@pytest_asyncio.fixture
async def seeded(test_db: AsyncSession, test_user, test_user_two, conversation_factory):
    """A message from user two, translated into English three different ways.

    `test_user` reads English and `test_user_two` reads Vietnamese
    (tests/conftest.py), so the English rows are the ones `test_user` chooses
    between and the Vietnamese one is what the sender is shown in a direct
    conversation.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])

    message = Message(
        client_message_id=f"c-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Anh xem lai UI giup em nhe",
        source_language="vi",
    )
    test_db.add(message)
    await test_db.flush()

    for profile, text in (
        ("peer", "Please take another look at the UI"),
        ("senior", "Would you mind reviewing the UI again"),
        ("client", "Could you kindly review the user interface again"),
    ):
        test_db.add(
            TranslationResult(
                message_id=message.id,
                target_language="en",
                honorific_profile=profile,
                translated_text=text,
                model="test-model",
                latency_ms=1,
                is_fallback=False,
            )
        )
    await test_db.commit()
    return conversation, message


async def _set_profile(
    test_db: AsyncSession, conversation_id: str, user_id: str, profile: str
) -> None:
    """Give one member a standing in one conversation."""
    test_db.add(
        ParticipantProfile(
            conversation_id=conversation_id,
            user_id=user_id,
            honorific_profile=profile,
            inferred_by="llm",
            confidence=90,
        )
    )
    await test_db.commit()


@pytest.mark.asyncio
async def test_history_returns_one_translation_per_language(
    client, test_db, test_user, test_user_headers, seeded
) -> None:
    """Three rows for English must not reach the client as three candidates it
    has no rule for choosing between."""
    conversation, _ = seeded

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )

    translations = response.json()[0]["translations"]
    assert [row["target_language"] for row in translations] == ["en"]


@pytest.mark.asyncio
async def test_history_serves_the_translation_written_for_the_readers_standing(
    client, test_db, test_user, test_user_headers, seeded
) -> None:
    conversation, _ = seeded
    await _set_profile(test_db, conversation.id, test_user.id, "client")

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )

    translation = response.json()[0]["translations"][0]
    assert translation["honorific_profile"] == "client"
    assert translation["translated_text"].startswith("Could you kindly")


@pytest.mark.asyncio
async def test_history_falls_back_to_neutral_when_the_readers_standing_is_missing(
    client, test_db, test_user, test_user_headers, seeded
) -> None:
    """The state a conversation lands in whenever a profile is re-inferred after
    its messages were translated. The reader must still see something."""
    conversation, _ = seeded
    # Nothing was ever translated for a junior reader of this message.
    await _set_profile(test_db, conversation.id, test_user.id, "junior")

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )

    translation = response.json()[0]["translations"][0]
    assert translation["honorific_profile"] == "peer"
    assert translation["translated_text"] == "Please take another look at the UI"


@pytest.mark.asyncio
async def test_a_direct_sender_is_still_shown_the_translation_the_other_reads(
    client, test_db, test_user, test_user_two, conversation_factory
) -> None:
    """The sender's own bubble carries the rating and edit controls (ADR-19),
    and it can only do that if the row the *recipient* reads comes back to the
    sender as well. Collapsing per language must not drop it."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="direct"
    )
    message = Message(
        client_message_id=f"c-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="Could you review the UI again",
        source_language="en",
    )
    test_db.add(message)
    await test_db.flush()
    test_db.add(
        TranslationResult(
            message_id=message.id,
            target_language="vi",
            honorific_profile="senior",
            translated_text="Anh xem lai giao dien giup em nhe",
            model="test-model",
            latency_ms=1,
            is_fallback=False,
        )
    )
    await test_db.commit()
    await _set_profile(test_db, conversation.id, test_user_two.id, "senior")

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=auth_headers_for_user(test_user),
    )

    translations = response.json()[0]["translations"]
    assert len(translations) == 1
    assert translations[0]["target_language"] == "vi"
    # Chosen with the recipient's standing, not the sender's: the sender does
    # not read Vietnamese, so their own standing says nothing about this row.
    assert translations[0]["honorific_profile"] == "senior"


@pytest.mark.asyncio
async def test_conversation_members_carry_their_standing_in_that_conversation(
    client, test_db, test_user, test_user_headers, test_user_two, conversation_factory
) -> None:
    """The client reads its own standing from here in order to match it against
    the translations array, so it has to be present and per conversation."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    await _set_profile(test_db, conversation.id, test_user.id, "junior")

    response = await client.get("/api/v1/conversations", headers=test_user_headers)

    listed = next(
        row for row in response.json() if row["id"] == conversation.id
    )
    standings = {
        member["id"]: member["honorific_profile"] for member in listed["members"]
    }
    assert standings[test_user.id] == "junior"
    # Nobody inferred anything for the other member, and that reads as neutral
    # rather than as an error — the state every conversation starts in.
    assert standings[test_user_two.id] == "peer"


@pytest.mark.asyncio
async def test_the_sidebar_preview_uses_the_readers_own_standing(
    client, test_db, test_user, test_user_headers, seeded
) -> None:
    """The preview and the conversation must agree. This used to be a dict
    keyed by message id, so whichever row came back last won, and the two could
    show different wordings of the same message."""
    conversation, _ = seeded
    await _set_profile(test_db, conversation.id, test_user.id, "senior")

    response = await client.get("/api/v1/conversations", headers=test_user_headers)

    listed = next(row for row in response.json() if row["id"] == conversation.id)
    assert listed["last_message"] == "Would you mind reviewing the UI again"
