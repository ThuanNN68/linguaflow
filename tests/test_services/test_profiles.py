"""Tests for resolving a reader's standing and the translation it entitles them to.

The fallback ladder in `select_for_reader` is the part worth guarding. Standings
are inferred and can change, while a translation keeps the standing it was
written under forever, so the two drift apart by design. Every step of the
ladder exists to stop that drift from showing a reader nothing at all — and a
regression there fails silently, because "no translation" renders as the
original text, which is also what a genuine translation failure renders as.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Conversation, ParticipantProfile, User
from src.services.identity.profiles import (
    profile_for,
    resolve_profiles,
    resolve_profiles_for_conversations,
    select_for_reader,
)


@dataclass
class Row:
    """A stand-in for a translation, carrying only what the ladder reads.

    `select_for_reader` is typed against a Protocol precisely so this works:
    the rule is pure, and testing it through the ORM would say more about
    SQLAlchemy than about the rule.
    """

    target_language: str
    honorific_profile: str
    label: str = ""


def test_a_reader_is_shown_the_translation_written_for_their_standing():
    """The ordinary case: an exact bucket match wins over everything else."""
    rows = [
        Row("en", "peer", "neutral"),
        Row("en", "client", "formal"),
        Row("en", "junior", "casual"),
    ]

    chosen = select_for_reader(rows, target_language="en", honorific_profile="client")

    assert chosen.label == "formal"


def test_a_missing_standing_falls_back_to_the_neutral_one():
    """A profile inferred after the translation was written must not blank it."""
    rows = [Row("en", "peer", "neutral"), Row("en", "junior", "casual")]

    chosen = select_for_reader(rows, target_language="en", honorific_profile="client")

    assert chosen.label == "neutral"


def test_a_reader_is_shown_some_standing_rather_than_nothing():
    """Last resort. A slightly wrong register beats an untranslated message,
    which is what the reader would otherwise be left holding."""
    rows = [Row("en", "senior", "deferential"), Row("en", "junior", "casual")]

    chosen = select_for_reader(rows, target_language="en", honorific_profile="client")

    assert chosen.label == "deferential"


def test_the_last_resort_follows_the_order_it_was_given():
    """Callers order by created_at, so "any" resolves to the oldest and two
    consecutive reads cannot disagree."""
    rows = [Row("en", "junior", "older"), Row("en", "senior", "newer")]

    chosen = select_for_reader(rows, target_language="en", honorific_profile="client")

    assert chosen.label == "older"


def test_a_language_nobody_translated_into_selects_nothing():
    """None is the signal to show the original text, and it has to be reachable."""
    rows = [Row("en", "peer"), Row("ja", "peer")]

    assert select_for_reader(rows, target_language="vi", honorific_profile="peer") is None


def test_another_readers_language_is_never_selected():
    """The ladder relaxes the standing, never the language: handing a Vietnamese
    reader the English text would look like a translation that silently failed."""
    rows = [Row("en", "client", "english")]

    assert select_for_reader(rows, target_language="vi", honorific_profile="client") is None


def test_a_member_with_no_inferred_profile_reads_as_peer():
    """Every conversation is in this state until it has enough messages to
    infer from, so this is the common path and not an edge case."""
    assert profile_for({}, "u1") == "peer"
    assert profile_for({"u1": ""}, "u1") == "peer"
    assert profile_for({"u1": "senior"}, "u1") == "senior"


@pytest_asyncio.fixture
async def two_conversations(test_db: AsyncSession) -> tuple[User, User, list[str]]:
    """One reader in two conversations, holding a different standing in each."""
    reader = User(
        email=f"reader-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="member",
        preferred_language="vi",
        interface_language="vi",
    )
    other = User(
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="member",
        preferred_language="en",
        interface_language="en",
    )
    test_db.add_all([reader, other])
    await test_db.flush()

    internal = Conversation(type="group", created_by=reader.id)
    with_client = Conversation(type="group", created_by=reader.id)
    test_db.add_all([internal, with_client])
    await test_db.flush()

    test_db.add_all(
        [
            ParticipantProfile(
                conversation_id=internal.id,
                user_id=reader.id,
                honorific_profile="junior",
                inferred_by="llm",
                confidence=90,
            ),
            ParticipantProfile(
                conversation_id=internal.id,
                user_id=other.id,
                honorific_profile="senior",
                inferred_by="llm",
                confidence=85,
            ),
            ParticipantProfile(
                conversation_id=with_client.id,
                user_id=reader.id,
                honorific_profile="client",
                inferred_by="llm",
                confidence=70,
            ),
        ]
    )
    await test_db.commit()
    return reader, other, [internal.id, with_client.id]


@pytest.mark.asyncio
async def test_resolving_a_conversation_returns_a_standing_per_member(
    test_db: AsyncSession, two_conversations
) -> None:
    reader, other, (internal_id, _) = two_conversations

    profiles = await resolve_profiles(test_db, internal_id)

    assert profiles == {reader.id: "junior", other.id: "senior"}


@pytest.mark.asyncio
async def test_one_account_holds_different_standings_in_different_conversations(
    test_db: AsyncSession, two_conversations
) -> None:
    """The reason the profile hangs off (conversation, user) rather than user:
    the same person is a junior colleague in one thread and a client in another."""
    reader, other, conversation_ids = two_conversations

    grouped = await resolve_profiles_for_conversations(test_db, conversation_ids)

    assert profile_for(grouped[conversation_ids[0]], reader.id) == "junior"
    assert profile_for(grouped[conversation_ids[1]], reader.id) == "client"
    # And the batch form still reports the other members, which the list
    # endpoint needs in order to render everyone in the conversation.
    assert profile_for(grouped[conversation_ids[0]], other.id) == "senior"


@pytest.mark.asyncio
async def test_batching_conversations_leaves_unprofiled_members_to_the_default(
    test_db: AsyncSession, two_conversations
) -> None:
    """The second conversation has no row for the other account, and that has to
    read as `peer` rather than raise."""
    _, other, conversation_ids = two_conversations

    grouped = await resolve_profiles_for_conversations(test_db, conversation_ids)

    assert profile_for(grouped[conversation_ids[1]], other.id) == "peer"
    assert await resolve_profiles_for_conversations(test_db, []) == {}


@pytest.mark.asyncio
async def test_resolving_a_conversation_nobody_has_been_profiled_in_is_empty(
    test_db: AsyncSession,
) -> None:
    """Not an error. Callers read through `profile_for`, which supplies `peer`."""
    assert await resolve_profiles(test_db, str(uuid.uuid4())) == {}
    assert await resolve_profiles(test_db, "") == {}
