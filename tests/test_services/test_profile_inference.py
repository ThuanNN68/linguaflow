"""Tests for inferring what a conversation is about and who is in it.

The cadence is what these mostly pin. It is the part with no visible symptom
when it goes wrong: inferring too eagerly spends quota and makes the register
change mid-thread, inferring too rarely leaves translations neutral forever, and
neither shows up as an error. The lock is worth guarding for a harder reason —
it is permanent, and there is no way for a person to undo it (ADR-24).

The model is always faked. Fixtures live in this file rather than
tests/conftest.py, which is shared across all feature areas.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ConversationProfile, Message, ParticipantProfile
from src.services.intelligence import profile_inference
from src.services.intelligence.profile_inference import (
    MESSAGES_BETWEEN_RUNS,
    MIN_MESSAGES_BEFORE_FIRST_RUN,
    STABLE_RUNS_BEFORE_LOCK,
    _is_due,
    _parse_inference,
    _run,
)


class _Profile:
    """A stand-in for the stored row, carrying only what the cadence reads."""

    def __init__(self, *, last_run: int = 0, locked: bool = False) -> None:
        self.message_count_at_last_run = last_run
        self.locked_at = "2026-08-20" if locked else None


def test_a_conversation_too_young_to_read_is_left_alone():
    """Below the threshold there is nothing to infer from, and a guess made
    there would be locked in later by the stability counter."""
    assert _is_due(None, MIN_MESSAGES_BEFORE_FIRST_RUN - 1) is False
    assert _is_due(None, MIN_MESSAGES_BEFORE_FIRST_RUN) is True


def test_inference_waits_a_full_interval_between_runs():
    """Scheduled on every message, so without this it would run on every one."""
    profile = _Profile(last_run=MIN_MESSAGES_BEFORE_FIRST_RUN)
    just_short = MIN_MESSAGES_BEFORE_FIRST_RUN + MESSAGES_BETWEEN_RUNS - 1

    assert _is_due(profile, just_short) is False
    assert _is_due(profile, just_short + 1) is True


def test_a_locked_conversation_is_never_inferred_again():
    """The point of locking: past this, no amount of new traffic spends quota."""
    assert _is_due(_Profile(locked=True), 10_000) is False


def test_an_answer_naming_an_unknown_standing_is_discarded_whole():
    """Not partially accepted. A half-understood answer would be written and
    then counted towards the stability that locks the profile permanently."""
    assert _parse_inference('{"participants": {"U01": "boss"}}', {"U01": "u1"}) is None
    assert _parse_inference("not json at all", {"U01": "u1"}) is None
    assert _parse_inference("", {"U01": "u1"}) is None


def test_a_scope_outside_the_closed_vocabulary_is_read_as_applying_everywhere():
    """The glossary lookup compares scopes by equality, so a free-text phrase
    files the conversation under a scope no entry is ever filed under. Empty is
    the honest answer and it still leaves the standings, which are judged
    separately and are the more valuable half."""
    parsed = _parse_inference(
        '{"domain": "contract negotiation", "audience": "an external client", '
        '"participants": {"U01": "peer"}}',
        {"U01": "u1"},
    )

    assert parsed is not None
    assert (parsed["domain"], parsed["audience"]) == ("", "")
    assert parsed["standings"] == {"u1": "peer"}


def test_a_scope_is_accepted_whatever_case_the_model_answers_in():
    """A model that answers `Client` means the same thing as one that answers
    `client`, and losing the scope over a capital letter would be a silent
    downgrade to the catch-all entries."""
    parsed = _parse_inference(
        '{"domain": " Engineering ", "audience": "Client", '
        '"participants": {"U01": "peer"}}',
        {"U01": "u1"},
    )

    assert parsed is not None
    assert (parsed["domain"], parsed["audience"]) == ("engineering", "client")


def test_an_invented_speaker_is_ignored_without_losing_the_rest():
    """A model that hallucinates a U09 must not cost the standings it got right;
    there is simply no row to write for a speaker nobody spoke as."""
    parsed = _parse_inference(
        '{"participants": {"U01": "senior", "U09": "client"}}', {"U01": "u1"}
    )

    assert parsed is not None
    assert parsed["standings"] == {"u1": "senior"}


def fake_llm(payload: dict) -> AsyncMock:
    """A model that answers with one canned JSON object."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        return_value=type("Msg", (), {"content": json.dumps(payload)})()
    )
    return llm


def session_factory_for_tests():
    """The session maker conftest points at the current test schema."""
    import tests.conftest as conftest_module

    return conftest_module.test_async_session_maker


@pytest_asyncio.fixture
async def talkative(test_db: AsyncSession, test_user, test_user_two, conversation_factory):
    """A conversation with enough messages to be worth inferring from."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="group"
    )
    # Timestamps written explicitly. Committed together they would all take the
    # transaction's clock, leaving the tiebreak to a random uuid — and the
    # aliases are assigned in order of first appearance, so U01 would be
    # whichever row the database happened to sort first.
    base = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    for index in range(MIN_MESSAGES_BEFORE_FIRST_RUN):
        speaker = test_user if index % 2 == 0 else test_user_two
        test_db.add(
            Message(
                client_message_id=f"m-{uuid.uuid4().hex[:8]}",
                conversation_id=conversation.id,
                sender_id=speaker.id,
                original_text=f"Message number {index}",
                source_language="en",
                created_at=base + timedelta(minutes=index),
            )
        )
    await test_db.commit()
    return conversation


async def _infer(conversation_id: str, payload: dict) -> None:
    """Run one inference pass against a canned answer."""
    llm = fake_llm(payload)
    await _run(
        conversation_id=conversation_id,
        session_factory=session_factory_for_tests(),
        llm_factory=lambda: llm,
    )


async def add_messages(
    test_db, conversation, count: int, *, start: int, speakers=None
) -> None:
    """Append `count` messages, enough to make another run fall due.

    Written out rather than nudging `message_count_at_last_run` by hand: the
    counter and the interval are the mechanism under test, and reaching past
    them to set up the next run would leave the test passing whatever the
    interval was changed to.
    """
    base = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    voices = list(speakers or [conversation.created_by])
    for index in range(count):
        test_db.add(
            Message(
                client_message_id=f"m-{uuid.uuid4().hex[:8]}",
                conversation_id=conversation.id,
                sender_id=voices[index % len(voices)],
                original_text=f"Follow-up {start + index}",
                source_language="en",
                created_at=base + timedelta(minutes=start + index),
            )
        )
    await test_db.commit()


@pytest.mark.asyncio
async def test_a_first_run_stores_the_subject_area_and_every_standing(
    test_db, talkative, test_user, test_user_two
):
    await _infer(
        talkative.id,
        {
            "domain": "engineering",
            "audience": "client",
            "participants": {"U01": "junior", "U02": "client"},
            "rationale": "One side reports progress, the other approves it.",
        },
    )

    profile = await test_db.scalar(
        select(ConversationProfile)
        .where(ConversationProfile.conversation_id == talkative.id)
        # The inference ran in its own session; without this the identity map
        # hands back the instance this session loaded first, attributes and all.
        .execution_options(populate_existing=True)
    )
    assert (profile.domain, profile.audience) == ("engineering", "client")
    standings = {
        row.user_id: row.honorific_profile
        for row in (
            await test_db.scalars(
                select(ParticipantProfile).where(
                    ParticipantProfile.conversation_id == talkative.id
                )
            )
        ).all()
    }
    # U01 is whoever spoke first, which the fixture makes test_user.
    assert standings == {test_user.id: "junior", test_user_two.id: "client"}


@pytest.mark.asyncio
async def test_a_conversation_locks_once_three_runs_agree(
    test_db, talkative, test_user, test_user_two
):
    """The lock is the mechanism that bounds the cost, and it is permanent."""
    answer = {
        "domain": "engineering",
        "audience": "internal",
        "participants": {"U01": "peer", "U02": "peer"},
        "rationale": "Nobody defers to anybody.",
    }

    for run in range(STABLE_RUNS_BEFORE_LOCK):
        if run:
            await add_messages(
                test_db,
                talkative,
                MESSAGES_BETWEEN_RUNS,
                start=run * 100,
                speakers=[test_user.id, test_user_two.id],
            )
        await _infer(talkative.id, answer)

    profile = await test_db.scalar(
        select(ConversationProfile)
        .where(ConversationProfile.conversation_id == talkative.id)
        # The inference ran in its own session; without this the identity map
        # hands back the instance this session loaded first, attributes and all.
        .execution_options(populate_existing=True)
    )
    assert profile.consecutive_stable_runs >= STABLE_RUNS_BEFORE_LOCK
    assert profile.locked_at is not None


@pytest.mark.asyncio
async def test_one_participant_changing_category_resets_the_stability_counter(
    test_db, talkative
):
    """Strict on purpose: locking means the conclusion has settled, and a
    conclusion that still moves for one person has not."""
    first = {
        "domain": "engineering",
        "audience": "internal",
        "participants": {"U01": "peer", "U02": "peer"},
        "rationale": "Nobody defers.",
    }
    await _infer(talkative.id, first)

    await add_messages(test_db, talkative, MESSAGES_BETWEEN_RUNS, start=100)

    changed = {**first, "participants": {"U01": "peer", "U02": "senior"}}
    await _infer(talkative.id, changed)

    profile = await test_db.scalar(
        select(ConversationProfile)
        .where(ConversationProfile.conversation_id == talkative.id)
        # The inference ran in its own session; without this the identity map
        # hands back the instance this session loaded first, attributes and all.
        .execution_options(populate_existing=True)
    )
    assert profile.consecutive_stable_runs == 1
    assert profile.locked_at is None


@pytest.mark.asyncio
async def test_a_model_that_answers_with_rubbish_changes_nothing(test_db, talkative):
    """Translation carries on with whatever profile it had, or none."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=type("Msg", (), {"content": "I think..."})())

    await _run(
        conversation_id=talkative.id,
        session_factory=session_factory_for_tests(),
        llm_factory=lambda: llm,
    )

    assert (
        await test_db.scalar(
            select(ConversationProfile)
            .where(ConversationProfile.conversation_id == talkative.id)
            .execution_options(populate_existing=True)
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_model_that_raises_leaves_the_conversation_untouched(
    test_db, talkative
):
    """Nothing here may propagate: it runs detached from a request that has
    already delivered the message."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("provider is down"))

    await _run(
        conversation_id=talkative.id,
        session_factory=session_factory_for_tests(),
        llm_factory=lambda: llm,
    )

    assert (
        await test_db.scalar(
            select(ConversationProfile)
            .where(ConversationProfile.conversation_id == talkative.id)
            .execution_options(populate_existing=True)
        )
        is None
    )


@pytest.mark.asyncio
async def test_the_model_is_not_called_before_the_conversation_has_enough_to_say(
    test_db, test_user, test_user_two, conversation_factory
):
    """The cadence has to be checked before the call, not after it."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="group"
    )
    test_db.add(
        Message(
            client_message_id="m-lonely",
            conversation_id=conversation.id,
            sender_id=test_user.id,
            original_text="Hello",
            source_language="en",
        )
    )
    await test_db.commit()
    llm = fake_llm({"participants": {"U01": "peer"}})

    await _run(
        conversation_id=conversation.id,
        session_factory=session_factory_for_tests(),
        llm_factory=lambda: llm,
    )

    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduling_without_a_conversation_does_nothing(test_db):
    """Guards the fire-and-forget entry point, which nobody awaits and whose
    failures nobody would see."""
    profile_inference.schedule_profile_inference(conversation_id="")

    assert not profile_inference._BACKGROUND_TASKS


@pytest.mark.asyncio
async def test_a_participant_who_goes_quiet_does_not_prevent_locking(
    test_db, talkative, test_user
):
    """A member who stops posting falls out of the transcript window, so the
    answer says nothing about them. That must read as "no news", not as a
    change of mind: treating it as a change would reset the counter every time
    and the conversation would keep spending a model call every twenty messages
    for the rest of its life.

    Their stored standing is left alone, not overwritten and not deleted.
    """
    both = {
        "domain": "engineering",
        "audience": "internal",
        "participants": {"U01": "senior", "U02": "junior"},
        "rationale": "One reviews, the other reports.",
    }
    await _infer(talkative.id, both)

    # From here only one person speaks, so only they appear in the window.
    for run in range(1, STABLE_RUNS_BEFORE_LOCK):
        await add_messages(
            test_db,
            talkative,
            MESSAGES_BETWEEN_RUNS,
            start=run * 100,
            speakers=[test_user.id],
        )
        await _infer(
            talkative.id,
            {**both, "participants": {"U01": "senior"}},
        )

    profile = await test_db.scalar(
        select(ConversationProfile)
        .where(ConversationProfile.conversation_id == talkative.id)
        .execution_options(populate_existing=True)
    )
    assert profile.locked_at is not None

    standings = {
        row.user_id: row.honorific_profile
        for row in (
            await test_db.scalars(
                select(ParticipantProfile)
                .where(ParticipantProfile.conversation_id == talkative.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    # Two rows still, including the one nobody said anything about.
    assert sorted(standings.values()) == ["junior", "senior"]
