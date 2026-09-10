"""Tests for the stats endpoint (NFR-03).

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from src.database.models import Message, TranslationAttempt

_next_message = itertools.count()


async def add_attempt(
    session,
    *,
    conversation_id,
    sender_id,
    outcome,
    days_ago=0,
    model_served="llama-3.3-70b-versatile",
    input_tokens=0,
    output_tokens=0,
):
    """Persist a message and one attempt against it."""
    created = datetime.now(UTC) - timedelta(days=days_ago)
    message = Message(
        # Unique per call: `client_message_id` is the idempotency key, and two
        # attempts in one test are two different messages.
        client_message_id=f"cm-{next(_next_message)}",
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text="Bản build mới đã lên staging",
        source_language="vi",
        created_at=created,
    )
    session.add(message)
    await session.commit()

    session.add(
        TranslationAttempt(
            message_id=message.id,
            target_language="en",
            source_language_declared="vi",
            source_language_detected="vi",
            outcome=outcome,
            provider="groq",
            model_served=model_served,
            detect_method="langdetect",
            total_ms=500,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            created_at=created,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_stats_requires_authentication(client):
    """The response exposes system-wide traffic, so it is not public."""
    assert (await client.get("/api/v1/stats")).status_code == 401


@pytest.mark.asyncio
async def test_stats_on_an_empty_database_returns_zeroes(client, test_admin_headers):
    """A fresh install must render a report, not an error."""
    response = await client.get("/api/v1/stats", headers=test_admin_headers)

    assert response.status_code == 200
    assert response.json()["total_attempts"] == 0
    assert response.json()["fallback_rate"] == 0.0


@pytest.mark.asyncio
async def test_stats_counts_outcomes_that_produced_no_translation(
    client, test_db, test_user, test_user_two, conversation_factory, test_admin_headers
):
    """The endpoint reports the same denominator the table was created for."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    for outcome in ("llm", "llm", "secondary", "timeout"):
        await add_attempt(
            test_db,
            conversation_id=conversation.id,
            sender_id=test_user.id,
            outcome=outcome,
        )

    body = (await client.get("/api/v1/stats", headers=test_admin_headers)).json()

    assert body["total_attempts"] == 4
    assert body["outcomes"]["llm"] == 2
    assert body["fallback_rate"] == 0.25
    # Timeout is retained as an operational outcome, but is neutral for the
    # completed-translation language-pair table.
    assert body["language_pairs"]["vi->en"]["count"] == 3


@pytest.mark.asyncio
async def test_a_priced_model_gets_a_dollar_cost_and_an_unpriced_one_does_not(
    client, test_db, test_user, test_user_two, conversation_factory, test_admin_headers
):
    """`llm_pricing.py` has an entry for `llama-3.3-70b-versatile`; a made-up
    model name never will. The unpriced one must come back `null`, not `0` —
    the two mean different things (see that module's docstring)."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    await add_attempt(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        outcome="llm",
        model_served="llama-3.3-70b-versatile",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    await add_attempt(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        outcome="llm",
        model_served="some-future-model",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )

    body = (await client.get("/api/v1/stats", headers=test_admin_headers)).json()

    priced = body["model_usage"]["llama-3.3-70b-versatile"]
    assert priced["input_tokens"] == 1_000_000
    assert priced["output_tokens"] == 1_000_000
    assert priced["input_price_per_million_usd"] == 0.59
    assert priced["output_price_per_million_usd"] == 0.79
    assert priced["cost_usd"] == pytest.approx(0.59 + 0.79, abs=1e-6)

    unpriced = body["model_usage"]["some-future-model"]
    assert unpriced["input_price_per_million_usd"] is None
    assert unpriced["cost_usd"] is None

    # The total only ever sums what it could actually price, and says so.
    assert body["total_cost_usd"] == pytest.approx(0.59 + 0.79, abs=1e-6)
    assert body["cost_usd_partial"] is True


@pytest.mark.asyncio
async def test_days_limits_the_window(
    client, test_db, test_user, test_user_two, conversation_factory, test_admin_headers
):
    """Last month's numbers must not be averaged into this week's."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    await add_attempt(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        outcome="llm",
        days_ago=30,
    )
    await add_attempt(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        outcome="timeout",
    )

    body = (await client.get("/api/v1/stats?days=7", headers=test_admin_headers)).json()

    assert body["window_days"] == 7
    assert body["total_attempts"] == 1
    assert body["outcomes"] == {"timeout": 1}


@pytest.mark.asyncio
async def test_an_unbounded_window_is_rejected(client, test_admin_headers):
    """Bounded so one request cannot ask for an unlimited history."""
    response = await client.get("/api/v1/stats?days=99999", headers=test_admin_headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_member_cannot_access_stats(client, test_user_headers):
    """System-wide operational metrics are restricted to administrators."""
    response = await client.get("/api/v1/stats", headers=test_user_headers)

    assert response.status_code == 403
