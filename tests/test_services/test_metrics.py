"""Tests for the shared aggregation helpers.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.database.models import Message, TranslationAttempt
from src.services.shared.metrics import (
    estimate_model_cost_usd,
    group_scores,
    percentile,
    summarize_attempts,
)

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def add_message(session, *, conversation_id, sender_id):
    """Persist one message for attempts to point at."""
    message = Message(
        client_message_id=f"cm-{conversation_id}-{sender_id}",
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text="Hôm nay deploy lúc mấy giờ?",
        source_language="vi",
        created_at=BASE_TIME,
    )
    session.add(message)
    await session.commit()
    return message


async def add_attempt(session, *, message_id, minute=0, **overrides):
    """Persist one attempt row, defaulting every field the test does not set."""
    fields = {
        "message_id": message_id,
        "target_language": "en",
        "source_language_declared": "vi",
        "source_language_detected": "vi",
        "outcome": "llm",
        "provider": "groq",
        "model_configured": "llama-3.3-70b-versatile",
        "model_served": "llama-3.3-70b-versatile",
        "detect_method": "langdetect",
        "llm_calls": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_ms": 500,
        "created_at": BASE_TIME + timedelta(minutes=minute),
    }
    fields.update(overrides)
    attempt = TranslationAttempt(**fields)
    session.add(attempt)
    await session.commit()
    return attempt


def test_percentile_of_nothing_is_zero():
    """A report over an empty database must print a number, not raise."""
    assert percentile([], 95) == 0.0


def test_percentile_of_one_value_is_that_value():
    """statistics.quantiles needs two points; one point is its own percentile."""
    assert percentile([420], 95) == 420.0


def test_percentile_interpolates_between_neighbours():
    """p50 of an even-length series falls between the two middle values."""
    assert percentile([10, 20, 30, 40], 50) == 25.0


def test_estimated_cost_supports_dated_model_snapshots():
    """A served snapshot uses the price of its model family."""
    cost = estimate_model_cost_usd("gpt-4o-mini-2024-07-18", 1_000_000, 1_000_000)

    assert cost == pytest.approx(0.75)


@pytest.mark.parametrize("served_name", ["gemini 3.7 flash", "gemini_3.7_flash"])
def test_estimated_cost_normalizes_provider_model_name_separators(served_name):
    """Provider display names must use the Gemini catalog price, not read as free."""
    cost = estimate_model_cost_usd(served_name, 1_000_000, 1_000_000)

    assert cost == pytest.approx(2.80)


def test_estimated_cost_ignores_unknown_models():
    """Unknown providers must not silently inherit an OpenAI price."""
    assert estimate_model_cost_usd("(none)", 1_000, 100) is None


def test_group_scores_reports_count_mean_and_passes():
    """Each bucket carries its sample size, so a mean of one is visible as such."""
    scored = [
        {"chat_type": "direct", "score": 1.0},
        {"chat_type": "direct", "score": 0.6},
        {"chat_type": "group", "score": 0.9},
    ]

    grouped = group_scores(scored, "chat_type", 0.8)

    assert grouped["direct"] == (2, 0.8, 1)
    assert grouped["group"] == (1, 0.9, 1)


def test_group_scores_labels_missing_values():
    """A sample missing the grouping field must still be counted somewhere."""
    grouped = group_scores([{"score": 1.0}], "chat_type", 0.8)

    assert grouped == {"(unknown)": (1, 1.0, 1)}


@pytest.mark.asyncio
async def test_summary_of_an_empty_log_is_all_zeroes(test_db):
    """No attempts recorded is a valid state, not an error."""
    summary = await summarize_attempts(test_db)

    assert summary.total == 0
    assert summary.fallback_rate == 0.0
    assert summary.language_pairs == {}


@pytest.mark.asyncio
async def test_fallback_rate_counts_outcomes_that_produce_no_translation(
    test_db, test_user, test_user_two, conversation_factory
):
    """The denominator is every attempt, which is the whole point of the table.

    Two of these four outcomes never reach `translation_results`. Computed from
    that table alone the fallback rate here would read 1/2; the honest figure is
    2/4, because a timeout is a translation the reader did not get either.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await add_message(
        test_db, conversation_id=conversation.id, sender_id=test_user.id
    )

    for minute, outcome in enumerate(["llm", "secondary", "original", "timeout"]):
        await add_attempt(
            test_db, message_id=message.id, minute=minute, outcome=outcome
        )

    summary = await summarize_attempts(test_db)

    assert summary.total == 4
    assert summary.outcomes == {"llm": 1, "secondary": 1, "original": 1, "timeout": 1}
    assert summary.fallback_rate == 0.5


@pytest.mark.asyncio
async def test_passthrough_attempts_are_excluded_from_pairs_and_models_only(
    test_db, test_user, test_user_two, conversation_factory
):
    """A passthrough is the bucket where the reader's own language matched the
    message, so no model and no fallback API ever ran (`route_after_detect`).
    That is not a translation that happened to be free — none was attempted —
    so it must not appear as a `"vi->vi"` row with an empty `models_served`
    bucket, and must not pull the headline latency down for work nobody did.

    It still belongs in `total`, `outcomes` and `fallback_rate`: that
    denominator is the entire reason `translation_attempts` records every
    no-translation exit rather than only the ones that produced a translation
    (ADR-16), and excluding it there would be overriding that decision, not
    applying it."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await add_message(
        test_db, conversation_id=conversation.id, sender_id=test_user.id
    )

    await add_attempt(test_db, message_id=message.id, minute=0, outcome="secondary")
    await add_attempt(
        test_db,
        message_id=message.id,
        minute=1,
        outcome="passthrough",
        target_language="vi",
        source_language_declared="vi",
        source_language_detected="vi",
        model_served="",
        input_tokens=0,
        output_tokens=0,
        total_ms=0,
    )

    summary = await summarize_attempts(test_db)

    assert summary.total == 2
    assert summary.outcomes == {"secondary": 1, "passthrough": 1}
    assert summary.fallback_rate == 0.5
    assert "vi->vi" not in summary.language_pairs
    assert list(summary.language_pairs) == ["vi->en"]
    assert "(none)" not in summary.models_served
    # The passthrough's 0ms must not enter the latency figures either — it
    # measures nothing, since no model was ever called.
    assert summary.total_ms_p50 == 500.0


@pytest.mark.asyncio
async def test_a_same_language_timeout_is_excluded_like_a_passthrough_would_be(
    test_db, test_user, test_user_two, conversation_factory
):
    """The exclusion above is keyed on the languages matching, not on
    `outcome == "passthrough"` — a same-language bucket that timed out or
    errored before the graph reached its routing decision is exactly as
    uninformative in `language_pairs` as an ordinary passthrough, and the
    outcome recorded for it is incidental to that."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await add_message(
        test_db, conversation_id=conversation.id, sender_id=test_user.id
    )

    await add_attempt(
        test_db,
        message_id=message.id,
        minute=0,
        outcome="timeout",
        target_language="vi",
        source_language_declared="vi-VN",
        source_language_detected=None,
        detect_method="",
        model_served="",
    )

    summary = await summarize_attempts(test_db)

    assert summary.total == 1
    assert summary.outcomes == {"timeout": 1}
    assert summary.language_pairs == {}
    assert summary.models_served == {}


@pytest.mark.asyncio
async def test_a_different_language_timeout_is_not_a_completed_pair_or_model(
    test_db, test_user, test_user_two, conversation_factory
):
    """A timeout is operational data, not a completed translation pair.

    It remains visible in totals/outcomes for reliability monitoring, but is
    neutral for pair success/failure and completed-translation latency.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await add_message(
        test_db, conversation_id=conversation.id, sender_id=test_user.id
    )

    await add_attempt(
        test_db,
        message_id=message.id,
        minute=0,
        outcome="timeout",
        target_language="vi",
        source_language_declared="en",
        source_language_detected=None,
        detect_method="",
        model_served="",
        total_ms=4000,
    )

    summary = await summarize_attempts(test_db)

    assert summary.total == 1
    assert summary.outcomes == {"timeout": 1}
    assert summary.language_pairs == {}
    assert summary.models_served == {}
    assert summary.total_ms_p50 == 0


@pytest.mark.asyncio
async def test_language_pair_uses_the_detected_source(
    test_db, test_user, test_user_two, conversation_factory
):
    """A misdeclared sender must be counted under the pair actually translated.

    Grouping by the declared language would file every corrected detection under
    the wrong pair, hiding exactly the cases ADR-11's second tier exists for.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await add_message(
        test_db, conversation_id=conversation.id, sender_id=test_user.id
    )

    await add_attempt(
        test_db,
        message_id=message.id,
        source_language_declared="vi",
        source_language_detected="en",
        target_language="fr",
    )

    assert list((await summarize_attempts(test_db)).language_pairs) == ["en->fr"]


@pytest.mark.asyncio
async def test_language_pair_falls_back_to_the_declared_source(
    test_db, test_user, test_user_two, conversation_factory
):
    """Detection can be skipped or fail; the attempt still belongs to a pair."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await add_message(
        test_db, conversation_id=conversation.id, sender_id=test_user.id
    )

    await add_attempt(
        test_db,
        message_id=message.id,
        source_language_detected=None,
        detect_method="",
        outcome="error",
    )

    summary = await summarize_attempts(test_db)

    assert list(summary.language_pairs) == ["vi->en"]
    assert summary.detect_methods == {"(none)": 1}


@pytest.mark.asyncio
async def test_since_limits_the_window(
    test_db, test_user, test_user_two, conversation_factory
):
    """`make metrics --since 7d` must not average in last month's model."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await add_message(
        test_db, conversation_id=conversation.id, sender_id=test_user.id
    )

    await add_attempt(test_db, message_id=message.id, minute=0, model_served="old")
    await add_attempt(test_db, message_id=message.id, minute=10, model_served="new")

    summary = await summarize_attempts(
        test_db, since=BASE_TIME + timedelta(minutes=5)
    )

    assert summary.total == 1
    assert summary.models_served == {"new": 1}


@pytest.mark.asyncio
async def test_tokens_and_latency_aggregate_across_attempts(
    test_db, test_user, test_user_two, conversation_factory
):
    """Token totals are what turns a trace count into a cost estimate."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await add_message(
        test_db, conversation_id=conversation.id, sender_id=test_user.id
    )

    await add_attempt(test_db, message_id=message.id, minute=0, total_ms=200)
    await add_attempt(test_db, message_id=message.id, minute=1, total_ms=800)

    summary = await summarize_attempts(test_db)

    assert summary.input_tokens == 200
    assert summary.output_tokens == 40
    assert summary.total_ms_p50 == 500.0
    assert summary.language_pairs["vi->en"].count == 2
