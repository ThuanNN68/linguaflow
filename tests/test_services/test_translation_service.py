"""Tests for the translation fan-out service (PR-6).

No LLM and no compiled graph: `graph_factory` is injected, so every case here
pins the service's own behaviour rather than the agent's. The agent has its own
76 tests.

Fixtures live in this file rather than tests/conftest.py, which is shared across
all feature areas.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from src.database.models import Message, TranslationAttempt, TranslationResult
from src.services.language import translation as translation_service
from src.services.language.translation import schedule_translations


class RecordingPublisher:
    """Captures what would have gone out over the socket."""

    def __init__(self) -> None:
        self.sent: list[tuple[tuple[str, ...], dict]] = []

    async def send_to_users(self, user_ids, payload) -> None:
        self.sent.append((tuple(user_ids), dict(payload)))

    def events_for(self, target_language: str) -> list[dict]:
        return [p for _, p in self.sent if p.get("target_language") == target_language]


def make_graph_factory(result_by_language: dict[str, dict]):
    """Build a graph_factory whose ainvoke returns a canned state per target."""

    def factory(_session, _message_id):
        class FakeGraph:
            async def ainvoke(self, state, config=None):
                canned = result_by_language.get(state["target_language"], {})
                return {**state, **canned}

        return FakeGraph()

    return factory


def failing_graph_factory(exc: Exception):
    def factory(_session, _message_id):
        class FakeGraph:
            async def ainvoke(self, state, config=None):
                raise exc

        return FakeGraph()

    return factory


def session_factory_for_tests():
    """The session maker conftest points at the current temp database.

    Read late — conftest reassigns it per test.
    """
    import tests.conftest as conftest_module

    return conftest_module.test_async_session_maker


@pytest.fixture(autouse=True)
def clear_translation_cache():
    """Keep process-local cache entries from crossing test boundaries."""
    translation_service._translation_cache.clear()
    yield
    translation_service._translation_cache.clear()


async def persist_message(
    session,
    *,
    conversation_id,
    sender_id,
    text,
    source_language,
    client_message_id="m1",
):
    message = Message(
        client_message_id=client_message_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text=text,
        source_language=source_language,
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def run_translations(*, message, publisher, graph_factory):
    """Schedule the work and wait for the background task to settle."""
    schedule_translations(
        message=message,
        publisher=publisher,
        session_factory=session_factory_for_tests(),
        graph_factory=graph_factory,
    )
    # Let the task run to completion; it is deliberately not awaited in production.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_translates_for_each_recipient_language(test_db, test_user, test_user_two, conversation_factory):
    """test_user reads en, test_user_two reads vi — one translation each way."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Deploy xong chua anh?",
        source_language="vi",
    )
    publisher = RecordingPublisher()

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=make_graph_factory(
            {
                "en": {
                    "source_language": "vi",
                    "translated_text": "Have you finished deploying?",
                    "model": "mock-model",
                    "latency_ms": 900,
                    "is_fallback": False,
                }
            }
        ),
    )

    # vi is the detected source, so the vi reader gets nothing — they have the original.
    assert publisher.events_for("vi") == []
    english = publisher.events_for("en")
    assert len(english) == 1
    assert english[0]["translated_text"] == "Have you finished deploying?"
    assert english[0]["conversation_id"] == conversation.id
    assert english[0]["is_fallback"] is False

    async with session_factory_for_tests()() as session:
        rows = (await session.scalars(select(TranslationResult))).all()
    assert [(r.target_language, r.is_fallback) for r in rows] == [("en", False)]


@pytest.mark.asyncio
async def test_sends_only_to_users_who_read_that_language(
    test_db, test_user, test_user_two, test_user_three, conversation_factory
):
    """en, vi and ja readers in one group; each event goes to its own audience."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two, test_user_three], conversation_type="group"
    )
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        text="Bọn em sẽ bàn giao trước thứ Sáu",
        source_language="vi",
    )
    publisher = RecordingPublisher()

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=make_graph_factory(
            {
                "en": {"source_language": "vi", "translated_text": "We will deliver before Friday"},
                "ja": {"source_language": "vi", "translated_text": "金曜日までに納品します"},
            }
        ),
    )

    recipients = {p["target_language"]: users for users, p in publisher.sent}
    assert recipients["en"] == (test_user.id,)
    assert recipients["ja"] == (test_user_three.id,)
    assert "vi" not in recipients


@pytest.mark.asyncio
async def test_writes_back_the_detected_source_language(test_db, test_user, test_user_two, conversation_factory):
    """The stored source_language is provisional until detection overrides it."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        text="The API is ready for testing",
        source_language="vi",  # provisional, and wrong
    )

    await run_translations(
        message=message,
        publisher=RecordingPublisher(),
        graph_factory=make_graph_factory(
            {"vi": {"source_language": "en", "translated_text": "API đã sẵn sàng để kiểm thử"}}
        ),
    )

    async with session_factory_for_tests()() as session:
        stored = await session.get(Message, message.id)
        assert stored.source_language == "en"


@pytest.mark.asyncio
async def test_publishes_nothing_when_the_agent_fails(test_db, test_user, test_user_two, conversation_factory):
    """A failed translation must leave the delivered original untouched."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Chào bạn nhé",
        source_language="vi",
    )
    publisher = RecordingPublisher()

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=failing_graph_factory(RuntimeError("agent exploded")),
    )

    assert publisher.sent == []
    async with session_factory_for_tests()() as session:
        assert (await session.scalars(select(TranslationResult))).all() == []
        assert (await session.get(Message, message.id)).original_text == "Chào bạn nhé"


@pytest.mark.asyncio
async def test_publishes_nothing_when_the_translation_is_empty(test_db, test_user, test_user_two, conversation_factory):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Chào bạn nhé",
        source_language="vi",
    )
    publisher = RecordingPublisher()

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=make_graph_factory({"en": {"source_language": "vi", "translated_text": "   "}}),
    )

    assert publisher.sent == []


@pytest.mark.asyncio
async def test_a_publisher_failure_does_not_escape_the_task(test_db, test_user, test_user_two, conversation_factory):
    """The translation is persisted, so an undelivered event is recoverable."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Chào bạn nhé",
        source_language="vi",
    )

    class BrokenPublisher:
        async def send_to_users(self, user_ids, payload):
            raise ConnectionError("socket registry down")

    await run_translations(
        message=message,
        publisher=BrokenPublisher(),
        graph_factory=make_graph_factory({"en": {"source_language": "vi", "translated_text": "Hello there"}}),
    )

    async with session_factory_for_tests()() as session:
        rows = (await session.scalars(select(TranslationResult))).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_rerunning_reuses_the_existing_translation_row(test_db, test_user, test_user_two, conversation_factory):
    """The unique constraint keeps one translation_id per message and language."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Chào bạn nhé",
        source_language="vi",
    )
    publisher = RecordingPublisher()
    graph_factory = make_graph_factory({"en": {"source_language": "vi", "translated_text": "Hello there"}})

    await run_translations(message=message, publisher=publisher, graph_factory=graph_factory)
    await run_translations(message=message, publisher=publisher, graph_factory=graph_factory)

    async with session_factory_for_tests()() as session:
        rows = (await session.scalars(select(TranslationResult))).all()
    assert len(rows) == 1

    ids = {p["translation_id"] for p in publisher.events_for("en")}
    assert len(ids) == 1


def mutating_graph_factory(result: dict, mutate):
    """A graph that changes the message while it is "translating".

    Stands in for the real gap between the graph starting and finishing — about
    1.5 seconds — during which the sender can edit or withdraw the message.
    """

    def factory(_session, message_id):
        class FakeGraph:
            async def ainvoke(self, state, config=None):
                async with session_factory_for_tests()() as session:
                    message = await session.get(Message, message_id)
                    mutate(message)
                    await session.commit()
                return {**state, **result}

        return FakeGraph()

    return factory


ENGLISH_RESULT = {
    "source_language": "vi",
    "translated_text": "Have you finished deploying?",
    "model": "mock-model",
    "latency_ms": 900,
    "is_fallback": False,
}


@pytest.mark.asyncio
async def test_translation_of_edited_text_is_neither_stored_nor_sent(
    test_db, test_user, test_user_two, conversation_factory
):
    """An edit mid-translation must not leave the old wording on screen.

    The unique constraint on (message_id, target_language) means this row would
    beat the retranslation's insert, so recipients would keep reading a
    translation of text the sender already replaced (F-06).
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Deploy xong chua anh?",
        source_language="vi",
    )
    publisher = RecordingPublisher()

    def edit(row):
        row.original_text = "Deploy xong chua chi?"

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=mutating_graph_factory(ENGLISH_RESULT, edit),
    )

    assert publisher.events_for("en") == []
    stored = (await test_db.scalars(select(TranslationResult).where(TranslationResult.message_id == message.id))).all()
    assert stored == []


@pytest.mark.asyncio
async def test_translation_of_a_withdrawn_message_is_neither_stored_nor_sent(
    test_db, test_user, test_user_two, conversation_factory
):
    """Contract §3.6 says a withdrawn message's text must not travel."""
    from datetime import UTC, datetime

    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Deploy xong chua anh?",
        source_language="vi",
    )
    publisher = RecordingPublisher()

    def withdraw(row):
        row.deleted_at = datetime.now(UTC)

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=mutating_graph_factory(ENGLISH_RESULT, withdraw),
    )

    assert publisher.events_for("en") == []
    stored = (await test_db.scalars(select(TranslationResult).where(TranslationResult.message_id == message.id))).all()
    assert stored == []


@pytest.mark.asyncio
async def test_direct_sender_also_receives_the_translation_of_their_own_message(
    test_db, test_user, test_user_two, conversation_factory
):
    """The one-to-one sender needs the translation their reader got (§4.4 rule 3).

    test_user_two reads vi and writes in vi, so grouping by reading language
    alone would send the English translation only to test_user. Without this the
    sender has nothing to toggle, rate or edit under their own bubble until the
    page is reloaded.
    """
    conversation = await conversation_factory(test_user_two, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        text="Chieu nay hop luc may gio?",
        source_language="vi",
    )
    publisher = RecordingPublisher()

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=make_graph_factory(
            {
                "en": {
                    "source_language": "vi",
                    "translated_text": "What time is the meeting this afternoon?",
                    "model": "mock-model",
                    "latency_ms": 700,
                    "is_fallback": False,
                }
            }
        ),
    )

    english_recipients = [
        recipients for recipients, payload in publisher.sent if payload.get("target_language") == "en"
    ]
    assert len(english_recipients) == 1
    assert set(english_recipients[0]) == {test_user.id, test_user_two.id}


@pytest.mark.asyncio
async def test_group_sender_is_left_out_of_a_language_they_do_not_read(
    test_db, test_user, test_user_two, test_user_three, conversation_factory
):
    """Groups keep the old routing: no controls there, so nothing extra to send.

    A group message has one translation per language and no single one belongs
    to the sender's bubble, so §3.10 shows them no controls and §4.4 rule 3
    stays as it was.
    """
    conversation = await conversation_factory(
        test_user_two,
        [test_user, test_user_two, test_user_three],
        conversation_type="group",
    )
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        text="Chieu nay hop luc may gio?",
        source_language="vi",
    )
    publisher = RecordingPublisher()

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=make_graph_factory(
            {
                "en": {
                    "source_language": "vi",
                    "translated_text": "What time is the meeting this afternoon?",
                    "model": "mock-model",
                    "latency_ms": 700,
                    "is_fallback": False,
                },
                "ja": {
                    "source_language": "vi",
                    "translated_text": "今日の午後の会議は何時ですか?",
                    "model": "mock-model",
                    "latency_ms": 800,
                    "is_fallback": False,
                },
            }
        ),
    )

    for recipients, payload in publisher.sent:
        if payload.get("target_language") in {"en", "ja"}:
            assert test_user_two.id not in recipients


def counting_graph_factory(result_by_language: dict[str, dict]):
    """Return a canned graph factory and the target languages it was asked for."""
    calls: list[str] = []

    def factory(_session, _message_id):
        class FakeGraph:
            async def ainvoke(self, state, config=None):
                calls.append(state["target_language"])
                return {**state, **result_by_language.get(state["target_language"], {})}

        return FakeGraph()

    return factory, calls


PRIMARY_EN_TO_VI = {
    "source_language": "en",
    "translated_text": "Chao buoi sang",
    "model": "primary-model",
    "latency_ms": 42,
    "is_fallback": False,
    "telemetry": {"outcome": "llm", "detect_method": "langdetect"},
}


@pytest.mark.asyncio
async def test_translation_cache_hit_skips_graph_and_attempt(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    """A safely detected short phrase reuses its completed primary translation."""
    monkeypatch.setattr(translation_service, "_detect_local", lambda _text: "en")
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    first = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
        client_message_id="cache-1",
    )
    second = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
        client_message_id="cache-2",
    )
    graph_factory, calls = counting_graph_factory({"vi": PRIMARY_EN_TO_VI})
    publisher = RecordingPublisher()

    await run_translations(message=first, publisher=publisher, graph_factory=graph_factory)
    await run_translations(message=second, publisher=publisher, graph_factory=graph_factory)

    assert calls.count("vi") == 1
    assert [event["model"] for event in publisher.events_for("vi")] == [
        "primary-model",
        "cache",
    ]
    async with session_factory_for_tests()() as session:
        attempts = (
            await session.scalars(select(TranslationAttempt).where(TranslationAttempt.target_language == "vi"))
        ).all()
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_translation_cache_long_text_is_not_cached(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    monkeypatch.setattr(translation_service, "_detect_local", lambda _text: "en")
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    text = "This phrase is deliberately longer than thirty characters"
    first = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text=text,
        source_language="en",
        client_message_id="long-1",
    )
    second = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text=text,
        source_language="en",
        client_message_id="long-2",
    )
    graph_factory, calls = counting_graph_factory({"vi": PRIMARY_EN_TO_VI})

    await run_translations(message=first, publisher=RecordingPublisher(), graph_factory=graph_factory)
    await run_translations(message=second, publisher=RecordingPublisher(), graph_factory=graph_factory)

    assert calls.count("vi") == 2
    assert translation_service._translation_cache == {}


@pytest.mark.asyncio
async def test_translation_cache_failure_falls_through_to_the_graph(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    class BrokenCache(dict):
        def get(self, key, default=None):
            raise RuntimeError("cache unavailable")

    monkeypatch.setattr(translation_service, "_detect_local", lambda _text: "en")
    monkeypatch.setattr(translation_service, "_translation_cache", BrokenCache())
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
    )
    graph_factory, calls = counting_graph_factory({"vi": PRIMARY_EN_TO_VI})
    publisher = RecordingPublisher()

    await run_translations(message=message, publisher=publisher, graph_factory=graph_factory)

    assert calls.count("vi") == 1
    assert len(publisher.events_for("vi")) == 1


@pytest.mark.asyncio
async def test_translation_cache_does_not_store_fallback_output(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    monkeypatch.setattr(translation_service, "_detect_local", lambda _text: "en")
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    first = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
        client_message_id="fallback-1",
    )
    second = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
        client_message_id="fallback-2",
    )
    fallback_result = {
        **PRIMARY_EN_TO_VI,
        "translated_text": "Good morning",
        "is_fallback": True,
        "telemetry": {"outcome": "original", "detect_method": "langdetect"},
    }
    graph_factory, calls = counting_graph_factory({"vi": fallback_result})

    await run_translations(message=first, publisher=RecordingPublisher(), graph_factory=graph_factory)
    await run_translations(message=second, publisher=RecordingPublisher(), graph_factory=graph_factory)

    assert calls.count("vi") == 2
    assert translation_service._translation_cache == {}


@pytest.mark.asyncio
async def test_translation_cache_hit_does_not_publish_a_withdrawn_message(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    """A cached value is still checked against the current persisted message."""
    from datetime import UTC, datetime

    monkeypatch.setattr(translation_service, "_detect_local", lambda _text: "en")
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
    )
    translation_service._translation_cache[
        translation_service._cache_key(conversation.id, "Good morning", "en", "vi", "peer")
    ] = ("Chao buoi sang", time.monotonic())
    message.deleted_at = datetime.now(UTC)
    await test_db.commit()
    graph_factory, calls = counting_graph_factory({"vi": PRIMARY_EN_TO_VI})
    publisher = RecordingPublisher()

    await run_translations(message=message, publisher=publisher, graph_factory=graph_factory)

    assert calls.count("vi") == 0
    assert publisher.events_for("vi") == []


@pytest.mark.asyncio
async def test_translation_cache_is_scoped_to_the_conversation(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    monkeypatch.setattr(translation_service, "_detect_local", lambda _text: "en")
    first_conversation = await conversation_factory(test_user, [test_user, test_user_two])
    second_conversation = await conversation_factory(test_user, [test_user, test_user_two])
    first = await persist_message(
        test_db,
        conversation_id=first_conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
        client_message_id="conversation-1",
    )
    second = await persist_message(
        test_db,
        conversation_id=second_conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
        client_message_id="conversation-2",
    )
    graph_factory, calls = counting_graph_factory({"vi": PRIMARY_EN_TO_VI})

    await run_translations(message=first, publisher=RecordingPublisher(), graph_factory=graph_factory)
    await run_translations(message=second, publisher=RecordingPublisher(), graph_factory=graph_factory)

    assert calls.count("vi") == 2


@pytest.mark.asyncio
async def test_translation_cache_detector_disagreement_runs_the_graph(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    monkeypatch.setattr(translation_service, "_detect_local", lambda _text: "fr")
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
    )
    translation_service._translation_cache[
        translation_service._cache_key(conversation.id, "Good morning", "en", "vi", "peer")
    ] = ("Chao buoi sang", time.monotonic())
    graph_factory, calls = counting_graph_factory({"vi": PRIMARY_EN_TO_VI})
    publisher = RecordingPublisher()

    await run_translations(message=message, publisher=publisher, graph_factory=graph_factory)

    assert calls.count("vi") == 1
    assert [event["model"] for event in publisher.events_for("vi")] == ["primary-model"]


@pytest.mark.asyncio
async def test_the_translation_cache_never_serves_one_standing_to_another(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    """A cached phrase written for a client must not be handed to a colleague.

    `_is_cacheable` admits only very short phrases, which is exactly the set
    where the address form carries the meaning, so a cache keyed by language
    alone would be wrong precisely where it is used most — and silently, since
    a hit produces an ordinary-looking translation.
    """
    monkeypatch.setattr(translation_service, "_detect_local", lambda _text: "en")
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        text="Good morning",
        source_language="en",
    )
    # Seeded under a standing nobody in this conversation holds; the members
    # are unprofiled, so the fan-out asks for `peer`.
    translation_service._translation_cache[
        translation_service._cache_key(conversation.id, "Good morning", "en", "vi", "client")
    ] = ("Kinh chao quy khach", time.monotonic())
    graph_factory, calls = counting_graph_factory({"vi": PRIMARY_EN_TO_VI})
    publisher = RecordingPublisher()

    await run_translations(message=message, publisher=publisher, graph_factory=graph_factory)

    assert calls.count("vi") == 1
    assert [event["model"] for event in publisher.events_for("vi")] == ["primary-model"]
