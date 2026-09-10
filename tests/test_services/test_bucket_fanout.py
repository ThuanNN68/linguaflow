"""Tests for fanning a message out per (language, standing) rather than per language.

This is the change that first makes a message hold more than one translation
into the same language. Everything that reads those rows was made ready in an
earlier step; these pin the writing side.

No LLM and no compiled graph: `graph_factory` is injected, so each case
describes the service's own behaviour. Fixtures live in this file rather than
tests/conftest.py, which is shared across all feature areas.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from src.database.models import (
    Message,
    ParticipantProfile,
    TranslationAttempt,
    TranslationResult,
)
from src.services.language import translation as translation_service
from src.services.language.translation import schedule_translations


class RecordingPublisher:
    """Captures what would have gone out over the socket."""

    def __init__(self) -> None:
        self.sent: list[tuple[tuple[str, ...], dict]] = []

    async def send_to_users(self, user_ids, payload) -> None:
        self.sent.append((tuple(user_ids), dict(payload)))


def bucket_graph_factory(text_by_bucket: dict[tuple[str, str], str]):
    """A graph whose answer depends on the standing as well as the language.

    The shared helper in test_translation_service.py keys its canned results by
    target language alone, which cannot tell two standings apart — and telling
    them apart is the entire point here.
    """
    seen: list[tuple[str, str]] = []

    def factory(_session, _message_id):
        class FakeGraph:
            async def ainvoke(self, state, config=None):
                bucket = (state["target_language"], state["honorific_profile"])
                seen.append(bucket)
                if bucket not in text_by_bucket:
                    # No translated_text, so the service takes its "empty" exit
                    # and writes nothing. This is how the sender's own language
                    # is kept out of the way: the real graph routes it to
                    # passthrough, which this stand-in does not model.
                    return {**state}
                return {
                    **state,
                    "translated_text": text_by_bucket[bucket],
                    "model": "primary-model",
                    "latency_ms": 5,
                    "is_fallback": False,
                    "source_language": state["source_language"],
                    "telemetry": {"outcome": "llm", "detect_method": "skipped"},
                }

        return FakeGraph()

    return factory, seen


def session_factory_for_tests():
    """The session maker conftest points at the current test schema."""
    import tests.conftest as conftest_module

    return conftest_module.test_async_session_maker


@pytest.fixture(autouse=True)
def clear_translation_cache():
    """Keep process-local cache entries from crossing test boundaries."""
    translation_service._translation_cache.clear()
    yield
    translation_service._translation_cache.clear()


async def run_translations(*, message, publisher, graph_factory):
    """Schedule the work and wait for the background task to settle."""
    schedule_translations(
        message=message,
        publisher=publisher,
        session_factory=session_factory_for_tests(),
        graph_factory=graph_factory,
    )
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def two_vietnamese_readers(test_user, test_user_two, test_admin):
    """A sender who reads English and two members who both read Vietnamese.

    `test_admin` is used only for its language and has no bearing on the role;
    conftest happens to give it `preferred_language="vi"`.
    """
    return test_user, test_user_two, test_admin


async def _seed(test_db, conversation, sender, standings: dict[str, str]):
    """Store one message and give the named members a standing."""
    for user_id, profile in standings.items():
        test_db.add(
            ParticipantProfile(
                conversation_id=conversation.id,
                user_id=user_id,
                honorific_profile=profile,
                inferred_by="llm",
                confidence=90,
            )
        )
    message = Message(
        client_message_id="m-bucket",
        conversation_id=conversation.id,
        sender_id=sender.id,
        original_text="Please review the interface",
        source_language="en",
    )
    test_db.add(message)
    await test_db.commit()
    await test_db.refresh(message)
    return message


@pytest.mark.asyncio
async def test_two_standings_in_one_language_each_get_their_own_translation(
    test_db, two_vietnamese_readers, conversation_factory
):
    """The feature, at the level that costs money: one language, two model calls,
    because a senior colleague and a client are not owed the same sentence."""
    sender, reader, other_reader = two_vietnamese_readers
    conversation = await conversation_factory(
        sender, [sender, reader, other_reader], conversation_type="group"
    )
    message = await _seed(
        test_db,
        conversation,
        sender,
        {reader.id: "senior", other_reader.id: "client"},
    )
    graph_factory, seen = bucket_graph_factory(
        {
            ("vi", "senior"): "Anh xem lai giao dien giup em nhe",
            ("vi", "client"): "Kinh mong quy khach xem lai giao dien",
        }
    )

    await run_translations(
        message=message, publisher=RecordingPublisher(), graph_factory=graph_factory
    )

    vietnamese = sorted(bucket for bucket in seen if bucket[0] == "vi")
    assert vietnamese == [("vi", "client"), ("vi", "senior")]
    rows = (
        await test_db.scalars(
            select(TranslationResult).where(TranslationResult.message_id == message.id)
        )
    ).all()
    assert {row.honorific_profile: row.translated_text for row in rows} == {
        "senior": "Anh xem lai giao dien giup em nhe",
        "client": "Kinh mong quy khach xem lai giao dien",
    }


@pytest.mark.asyncio
async def test_members_sharing_a_standing_share_one_translation(
    test_db, two_vietnamese_readers, conversation_factory
):
    """The saving that keeps this affordable: the bucket is the unit, not the
    person, so two readers at the same standing cost one call between them."""
    sender, reader, other_reader = two_vietnamese_readers
    conversation = await conversation_factory(
        sender, [sender, reader, other_reader], conversation_type="group"
    )
    message = await _seed(
        test_db,
        conversation,
        sender,
        {reader.id: "junior", other_reader.id: "junior"},
    )
    graph_factory, seen = bucket_graph_factory({("vi", "junior"): "Em xem lai nhe"})
    publisher = RecordingPublisher()

    await run_translations(
        message=message, publisher=publisher, graph_factory=graph_factory
    )

    assert [bucket for bucket in seen if bucket[0] == "vi"] == [("vi", "junior")]
    rows = (
        await test_db.scalars(
            select(TranslationResult).where(TranslationResult.message_id == message.id)
        )
    ).all()
    assert len(rows) == 1
    # And both readers were addressed in the same event, with the same id.
    delivered = [payload for _, payload in publisher.sent]
    assert len(delivered) == 1
    assert {reader.id, other_reader.id} <= set(publisher.sent[0][0])


@pytest.mark.asyncio
async def test_a_member_nobody_profiled_is_translated_at_the_neutral_standing(
    test_db, test_user, test_user_two, conversation_factory
):
    """The common path. Profiles are only inferred once a conversation has
    enough messages, so every conversation starts here — an outer join that
    dropped these members would silently stop translating for them."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="group"
    )
    message = await _seed(test_db, conversation, test_user, {})
    graph_factory, seen = bucket_graph_factory({("vi", "peer"): "Xem lai giup nhe"})

    await run_translations(
        message=message, publisher=RecordingPublisher(), graph_factory=graph_factory
    )

    assert [bucket for bucket in seen if bucket[0] == "vi"] == [("vi", "peer")]


@pytest.mark.asyncio
async def test_each_bucket_records_its_own_attempt(
    test_db, two_vietnamese_readers, conversation_factory
):
    """Without the standing on the measurement row, one message produces several
    rows that look identical, and every latency percentile quietly averages
    different translations together."""
    sender, reader, other_reader = two_vietnamese_readers
    conversation = await conversation_factory(
        sender, [sender, reader, other_reader], conversation_type="group"
    )
    message = await _seed(
        test_db,
        conversation,
        sender,
        {reader.id: "senior", other_reader.id: "client"},
    )
    graph_factory, _ = bucket_graph_factory({})

    await run_translations(
        message=message, publisher=RecordingPublisher(), graph_factory=graph_factory
    )

    attempts = (
        await test_db.scalars(
            select(TranslationAttempt).where(
                TranslationAttempt.message_id == message.id,
                # The sender's own language is attempted too and recorded as
                # such; this case is about the two Vietnamese buckets.
                TranslationAttempt.target_language == "vi",
            )
        )
    ).all()
    assert sorted(attempt.honorific_profile for attempt in attempts) == [
        "client",
        "senior",
    ]
