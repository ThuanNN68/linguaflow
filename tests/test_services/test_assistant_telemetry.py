"""The Assistant Agent's measurement log (ADR-16's arrangement, ADR-40's agent).

Two things are worth holding in place here, and both are the kind that fail
quietly.

`classify` decides which bucket a run lands in, and the buckets are the report:
a run recorded as `empty` when it actually refused makes a permission problem
look like a quiet week. Its ordering is also load-bearing — a run that both
proposed and executed is one run, and counting it twice would make the
confirmation rate exceed one.

`record_attempt` must never be able to fail a run. A person waiting on an answer
must not lose it to a bookkeeping error, which is the rule NFR-02 sets for the
translation path and this inherits.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.database.models import ASSISTANT_OUTCOMES, AssistantAttempt, Message
from src.services.assistant.assistant_telemetry import classify, record_attempt

# --- classify ---------------------------------------------------------------


def test_a_refused_run_is_recorded_as_refused_not_as_empty():
    """Otherwise a permission problem reads as a quiet week."""
    outcome, code = classify({"missing_consent": "calendar_write"})

    assert (outcome, code) == ("refused", "calendar_write")


def test_a_run_that_carried_something_out_counts_once_as_executed():
    """It also had proposals; counting both would push the rate above one."""
    outcome, _ = classify({"proposals": [{"id": "p1"}], "executed": [{"id": "p1"}]})

    assert outcome == "executed"


def test_a_run_parked_at_the_gate_is_recorded_as_having_reached_it():
    outcome, _ = classify({"proposals": [{"id": "p1"}]})

    assert outcome == "proposed"


def test_asking_a_question_back_is_its_own_outcome():
    """Asking is a result, not the absence of one."""
    outcome, _ = classify({"clarification": "Cuộc họp nào?"})

    assert outcome == "clarified"


def test_a_run_that_answered_despite_a_failure_is_not_recorded_as_an_error():
    """Something failed on the way and the person still got what they asked for.

    That is exactly what the no-node-raises rule is for, and recording it as an
    error would make the graph's resilience look like a fault rate.
    """
    outcome, _ = classify(
        {"error": "RuntimeError: provider timeout", "summary": {"summary": "ok"}}
    )

    assert outcome == "answered"


def test_a_run_that_ended_with_nothing_and_an_error_is_recorded_as_an_error():
    outcome, code = classify({"error": "RuntimeError: provider timeout"})

    assert outcome == "error"
    assert code == "RuntimeError"


def test_an_error_code_is_short_enough_to_group_by():
    """The state holds a message, often with an id in it — every row its own group."""
    _, code = classify(
        {"error": "ConnectionError: could not reach https://api.example/v1/abc123"}
    )

    assert code == "ConnectionError"


def test_a_run_that_found_nothing_is_recorded_as_empty():
    outcome, _ = classify({})

    assert outcome == "empty"


def test_every_outcome_classify_can_return_is_one_the_database_accepts():
    """A value outside the vocabulary fails on a CheckConstraint, inside a commit."""
    states = [
        {"missing_consent": "read_conversations"},
        {"executed": [{"id": "p"}]},
        {"proposals": [{"id": "p"}]},
        {"clarification": "?"},
        {"summary": {"summary": "x"}},
        {"error": "boom"},
        {},
    ]

    assert {classify(state)[0] for state in states} <= set(ASSISTANT_OUTCOMES)


# --- record_attempt ---------------------------------------------------------


@pytest_asyncio.fixture
async def conversation(test_user, conversation_factory):
    return await conversation_factory(test_user, [test_user], "group", "telemetry")


@pytest.mark.asyncio
async def test_a_row_is_written_for_a_run_that_produced_nothing(
    test_db, test_user, conversation
):
    """The reason the table exists: those runs are the missing denominator."""
    await record_attempt(
        test_db,
        state={},
        conversation_id=conversation.id,
        user_id=test_user.id,
        total_ms=120,
    )

    row = await test_db.scalar(select(AssistantAttempt))
    assert row is not None
    assert row.outcome == "empty"
    assert row.total_ms == 120


@pytest.mark.asyncio
async def test_the_tools_a_run_called_are_recorded_in_order(
    test_db, test_user, conversation
):
    """"Which tool" is the question a failure raises, and a count cannot answer it."""
    await record_attempt(
        test_db,
        state={
            "observations": [
                {"tool": "search_old_messages", "ok": True, "summary": "x"},
                {"tool": "summarize_conversation", "ok": False, "summary": "y"},
            ],
            "summary": {"summary": "done"},
        },
        conversation_id=conversation.id,
        user_id=test_user.id,
        total_ms=900,
    )

    row = await test_db.scalar(select(AssistantAttempt))
    assert json.loads(row.tools_used) == [
        "search_old_messages",
        "summarize_conversation",
    ]
    assert (row.tool_calls, row.tools_failed) == (2, 1)


@pytest.mark.asyncio
async def test_the_replan_count_is_recorded_so_the_ceiling_can_be_judged(
    test_db, test_user, conversation
):
    """All runs at zero means the loop is not earning its cost; all at the top
    means runs are being cut off mid-thought."""
    await record_attempt(
        test_db,
        state={"replan_count": 3, "summary": {"summary": "x"}},
        conversation_id=conversation.id,
        user_id=test_user.id,
        total_ms=5000,
    )

    row = await test_db.scalar(select(AssistantAttempt))
    assert row.replans == 3


@pytest.mark.asyncio
async def test_the_share_of_context_that_came_from_retrieval_is_recorded(
    test_db, test_user, conversation
):
    """Zero everywhere means `assistant_chunks` is empty, which nothing else reports."""
    await record_attempt(
        test_db,
        state={
            "telemetry": {"memory_lines": 12, "memory_recalled": 4},
            "summary": {"summary": "x"},
        },
        conversation_id=conversation.id,
        user_id=test_user.id,
        total_ms=800,
    )

    row = await test_db.scalar(select(AssistantAttempt))
    assert (row.memory_lines, row.memory_recalled) == (12, 4)


@pytest.mark.asyncio
async def test_the_message_a_run_was_triggered_by_is_recorded(
    test_db, test_user, conversation
):
    message = Message(
        conversation_id=conversation.id,
        client_message_id="telemetry-1",
        sender_id=test_user.id,
        original_text="mình sẽ gửi báo cáo",
        source_language="vi",
    )
    test_db.add(message)
    await test_db.commit()

    await record_attempt(
        test_db,
        state={"telemetry": {"source_message_id": message.id}, "proposals": [{"id": "p"}]},
        conversation_id=conversation.id,
        user_id=test_user.id,
        total_ms=400,
    )

    row = await test_db.scalar(select(AssistantAttempt))
    assert row.source_message_id == message.id


@pytest.mark.asyncio
async def test_two_runs_of_the_same_request_are_two_rows(
    test_db, test_user, conversation
):
    """Asking the assistant the same thing twice is two runs, as with translation."""
    for _ in range(2):
        await record_attempt(
            test_db,
            state={"summary": {"summary": "x"}},
            conversation_id=conversation.id,
            user_id=test_user.id,
            total_ms=100,
        )

    rows = (await test_db.scalars(select(AssistantAttempt))).all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_recording_never_raises_into_the_run(test_db, test_user, conversation):
    """A person waiting on an answer must not lose it to a bookkeeping error.

    An outcome outside the vocabulary would violate the CheckConstraint on the
    way to the database — the failure this swallows.
    """
    await record_attempt(
        test_db,
        # `classify` cannot produce this, so it is forced by monkeypatching the
        # shape it reads: a state whose error is not a string still has to be
        # survivable.
        state={"error": object()},
        conversation_id=conversation.id,
        user_id=test_user.id,
        total_ms=1,
    )


@pytest.mark.asyncio
async def test_a_run_in_a_deleted_conversation_still_leaves_its_evidence(
    test_db, test_user, conversation
):
    """SET NULL, not CASCADE: deleting a conversation must not delete the record
    that the assistant was asked something in it."""
    from sqlalchemy import delete

    from src.database.models import Conversation

    await record_attempt(
        test_db,
        state={"summary": {"summary": "x"}},
        conversation_id=conversation.id,
        user_id=test_user.id,
        total_ms=100,
    )
    await test_db.execute(delete(Conversation).where(Conversation.id == conversation.id))
    await test_db.commit()

    row = await test_db.scalar(select(AssistantAttempt))
    assert row is not None
    assert row.conversation_id is None
