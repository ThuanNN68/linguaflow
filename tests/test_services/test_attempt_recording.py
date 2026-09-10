"""Tests that every way a translation can end leaves exactly one attempt row.

The four outcomes that produce no translation are the point of the table: a
fallback rate computed only over the successes divides by the wrong number. Each
of them is pinned here, because none of them is observable anywhere else.

Fixtures live in this file rather than tests/conftest.py, which is shared across
all feature areas.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from src.database.models import TranslationAttempt, TranslationResult
from src.services.language.translation import schedule_translations
from tests.test_services.test_translation_service import (
    RecordingPublisher,
    failing_graph_factory,
    make_graph_factory,
    persist_message,
    session_factory_for_tests,
)


class HangingGraph:
    """A graph that never finishes, so asyncio.wait_for gives up on it."""

    async def ainvoke(self, state, config=None):
        await asyncio.sleep(3600)


async def run_translations(*, message, publisher, graph_factory):
    """Schedule the work and wait for the background task to settle."""
    schedule_translations(
        message=message,
        publisher=publisher,
        session_factory=session_factory_for_tests(),
        graph_factory=graph_factory,
    )
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending, return_exceptions=True)


async def attempts_for(session, message_id) -> list[TranslationAttempt]:
    """Every attempt recorded against a message, oldest first."""
    rows = await session.execute(
        select(TranslationAttempt)
        .where(TranslationAttempt.message_id == message_id)
        .order_by(TranslationAttempt.target_language)
    )
    return list(rows.scalars())


async def setup_message(test_db, test_user, test_user_two, conversation_factory):
    """One Vietnamese message in a conversation with one English reader."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    return await persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        text="Deploy xong chưa anh?",
        source_language="vi",
    )


@pytest.mark.asyncio
async def test_a_successful_translation_is_recorded_with_its_telemetry(
    test_db, test_user, test_user_two, conversation_factory
):
    """The success row links to the translation and carries what was measured."""
    message = await setup_message(test_db, test_user, test_user_two, conversation_factory)

    await run_translations(
        message=message,
        publisher=RecordingPublisher(),
        graph_factory=make_graph_factory(
            {
                "en": {
                    "translated_text": "Is the deploy done?",
                    "model": "configured-model",
                    "telemetry": {
                        "outcome": "llm",
                        "detect_method": "langdetect",
                        "model_served": "llama-3.3-70b-versatile",
                        "llm_calls": 1,
                        "input_tokens": 120,
                        "output_tokens": 8,
                        "translate_ms": 640,
                    },
                }
            }
        ),
    )

    attempts = await attempts_for(test_db, message.id)
    english = next(a for a in attempts if a.target_language == "en")

    assert english.outcome == "llm"
    assert english.model_served == "llama-3.3-70b-versatile"
    assert english.input_tokens == 120
    assert english.translate_ms == 640
    assert english.total_ms >= 0

    translation = await test_db.scalar(
        select(TranslationResult).where(TranslationResult.target_language == "en")
    )
    assert english.translation_id == translation.id


@pytest.mark.asyncio
async def test_a_timeout_is_recorded(
    test_db, test_user, test_user_two, conversation_factory
):
    """A run that never returns used to leave no trace of having happened."""
    message = await setup_message(test_db, test_user, test_user_two, conversation_factory)

    from src.config import get_settings

    settings = get_settings()
    original = settings.translation_timeout_seconds
    object.__setattr__(settings, "translation_timeout_seconds", 1)
    try:
        await run_translations(
            message=message,
            publisher=RecordingPublisher(),
            graph_factory=lambda _s, _m: HangingGraph(),
        )
    finally:
        object.__setattr__(settings, "translation_timeout_seconds", original)

    outcomes = {a.outcome for a in await attempts_for(test_db, message.id)}
    assert outcomes == {"timeout"}


@pytest.mark.asyncio
async def test_a_raising_graph_is_recorded_as_an_error(
    test_db, test_user, test_user_two, conversation_factory
):
    """The graph is not supposed to raise; if it does, the attempt still counts."""
    message = await setup_message(test_db, test_user, test_user_two, conversation_factory)

    await run_translations(
        message=message,
        publisher=RecordingPublisher(),
        graph_factory=failing_graph_factory(RuntimeError("graph exploded")),
    )

    attempts = await attempts_for(test_db, message.id)
    assert {a.outcome for a in attempts} == {"error"}
    # Nothing was measured, but the language pair and provider still are.
    assert attempts[0].source_language_declared == "vi"
    assert attempts[0].translation_id is None


@pytest.mark.asyncio
async def test_a_passthrough_is_not_recorded_as_a_translation(
    test_db, test_user, test_user_two, conversation_factory
):
    """A same-language reader gets the original without a translation log row."""
    message = await setup_message(test_db, test_user, test_user_two, conversation_factory)

    await run_translations(
        message=message,
        publisher=RecordingPublisher(),
        graph_factory=make_graph_factory(
            {
                "en": {
                    "source_language": "en",
                    "translated_text": "Is the deploy done?",
                    "telemetry": {"outcome": "passthrough", "detect_method": "llm"},
                }
            }
        ),
    )

    attempts = await attempts_for(test_db, message.id)
    assert all(attempt.target_language != "en" for attempt in attempts)


@pytest.mark.asyncio
async def test_an_empty_translation_is_recorded(
    test_db, test_user, test_user_two, conversation_factory
):
    """The one exit that was previously silent in both the log and the database."""
    message = await setup_message(test_db, test_user, test_user_two, conversation_factory)

    await run_translations(
        message=message,
        publisher=RecordingPublisher(),
        graph_factory=make_graph_factory({"en": {"translated_text": "   "}}),
    )

    attempts = await attempts_for(test_db, message.id)
    assert next(a for a in attempts if a.target_language == "en").outcome == "empty"


@pytest.mark.asyncio
async def test_the_detected_language_is_only_recorded_when_detection_ran(
    test_db, test_user, test_user_two, conversation_factory
):
    """Otherwise the declared and detected columns agree by construction.

    Detection skipped on a short message passes the declared value straight
    through. Storing that as a detection would make the ADR-11 agreement rate
    read as a perfect score no matter how detection actually performed.
    """
    message = await setup_message(test_db, test_user, test_user_two, conversation_factory)

    await run_translations(
        message=message,
        publisher=RecordingPublisher(),
        graph_factory=make_graph_factory(
            {
                "en": {
                    "translated_text": "Is the deploy done?",
                    "telemetry": {"outcome": "llm", "detect_method": "skipped"},
                }
            }
        ),
    )

    attempts = await attempts_for(test_db, message.id)
    english = next(a for a in attempts if a.target_language == "en")

    assert english.detect_method == "skipped"
    assert english.source_language_detected is None
    assert english.source_language_declared == "vi"


@pytest.mark.asyncio
async def test_a_failure_to_record_does_not_lose_the_translation(
    test_db, test_user, test_user_two, conversation_factory, monkeypatch
):
    """Bookkeeping is not allowed to cost a reader their message (NFR-02)."""
    message = await setup_message(test_db, test_user, test_user_two, conversation_factory)
    publisher = RecordingPublisher()

    import src.services.language.translation as translation_module

    def explode(**_fields):
        """Stand in for the row the service tries to build, and refuse."""
        raise RuntimeError("column missing")

    # Replaces only the name the service calls, leaving the real mapped class
    # available for the query below to load rows with.
    monkeypatch.setattr(translation_module, "TranslationAttempt", explode)

    await run_translations(
        message=message,
        publisher=publisher,
        graph_factory=make_graph_factory({"en": {"translated_text": "Is the deploy done?"}}),
    )

    assert publisher.events_for("en")
    assert await attempts_for(test_db, message.id) == []
