"""Map-reduce summarisation for long conversations (ADR-38).

The LLM is replaced by a counter throughout. What is worth asserting here is not
the prose a model produces but the control flow around it: how many calls happen,
where the batch boundaries fall, and what survives when a call fails — and all
three are invisible in an end-to-end test against a real provider.
"""

from __future__ import annotations

import pytest

from src.agents.conversation_intelligence import summary as summary_module
from src.agents.conversation_intelligence.summary import (
    LONG_CONVERSATION_THRESHOLD,
    MAP_BATCH_MESSAGES,
    generate_long_conversation_summary,
)
from src.schemas.intelligence import ConversationSummaryResponse

pytestmark = pytest.mark.asyncio


def _lines(count: int) -> list[str]:
    return [f"[2026-08-28T09:{index:02d}:00] U01: line {index}" for index in range(count)]


def _response(text: str) -> ConversationSummaryResponse:
    return ConversationSummaryResponse(
        summary=text,
        key_points=[f"point from {text}"],
        decisions=[],
        open_items=[],
        message_count=1,
        target_language="en",
    )


@pytest.fixture
def recorded_map(monkeypatch):
    """Record every map-stage call and return a distinguishable summary for each."""
    calls: list[str] = []

    async def fake_single_pass(transcript, target_language, message_count, **kwargs):
        calls.append(transcript)
        return _response(f"batch{len(calls)}")

    monkeypatch.setattr(
        summary_module, "generate_conversation_summary", fake_single_pass
    )
    return calls


@pytest.fixture
def stub_reduce(monkeypatch):
    """Replace the reduce LLM call, recording the partials it was handed."""
    seen: dict[str, object] = {}

    async def fake_invoke(*, messages, **kwargs):
        seen["prompt"] = messages[-1].content

        class Payload:
            summary = "merged"
            key_points = ["merged point"]
            decisions = ["merged decision"]
            open_items = []

        return Payload()

    monkeypatch.setattr(summary_module, "invoke_with_repair", fake_invoke)
    monkeypatch.setattr(summary_module, "get_llm", lambda **kwargs: object())
    return seen


async def test_short_conversation_takes_the_single_pass_path(recorded_map):
    """One entry point, two behaviours — the caller does not choose between them."""
    result = await generate_long_conversation_summary(
        transcript_lines=_lines(10),
        target_language="en",
        message_count=10,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    assert len(recorded_map) == 1
    assert result.summary == "batch1"


async def test_conversation_at_the_threshold_still_takes_the_single_pass_path(
    recorded_map,
):
    await generate_long_conversation_summary(
        transcript_lines=_lines(LONG_CONVERSATION_THRESHOLD),
        target_language="en",
        message_count=LONG_CONVERSATION_THRESHOLD,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    assert len(recorded_map) == 1


async def test_long_conversation_summarises_every_batch_before_merging(
    recorded_map, stub_reduce
):
    """The middle of a long thread gets a pass in which it is the only thing read."""
    count = MAP_BATCH_MESSAGES * 3 + 5
    await generate_long_conversation_summary(
        transcript_lines=_lines(count),
        target_language="en",
        message_count=count,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    assert len(recorded_map) == 4


async def test_batches_are_cut_between_messages_never_inside_one(
    recorded_map, stub_reduce
):
    """A fragment whose speaker and timestamp are in the previous batch is useless."""
    await generate_long_conversation_summary(
        transcript_lines=_lines(MAP_BATCH_MESSAGES * 2),
        target_language="en",
        message_count=MAP_BATCH_MESSAGES * 2,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    for transcript in recorded_map:
        for line in transcript.splitlines():
            assert line.startswith("[2026-08-28T09:")


async def test_every_batch_reaches_the_reduce_stage(recorded_map, stub_reduce):
    count = MAP_BATCH_MESSAGES * 3
    await generate_long_conversation_summary(
        transcript_lines=_lines(count),
        target_language="en",
        message_count=count,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    prompt = stub_reduce["prompt"]
    assert all(f"batch{index}" in prompt for index in (1, 2, 3))


async def test_long_conversation_reports_the_merged_result_not_a_partial(
    recorded_map, stub_reduce
):
    count = MAP_BATCH_MESSAGES * 2
    result = await generate_long_conversation_summary(
        transcript_lines=_lines(count),
        target_language="en",
        message_count=count,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    assert result.summary == "merged"
    assert result.decisions == ["merged decision"]
    # The count describes the conversation, not the last batch read.
    assert result.message_count == count


async def test_a_failed_batch_is_left_out_rather_than_failing_the_summary(
    monkeypatch, stub_reduce
):
    """Losing forty messages costs part of a summary; raising would cost all of it."""
    calls = {"n": 0}

    async def flaky(transcript, target_language, message_count, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("provider timeout")
        return _response(f"batch{calls['n']}")

    monkeypatch.setattr(summary_module, "generate_conversation_summary", flaky)

    count = MAP_BATCH_MESSAGES * 3
    result = await generate_long_conversation_summary(
        transcript_lines=_lines(count),
        target_language="en",
        message_count=count,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    assert result.summary == "merged"
    assert "batch2" not in stub_reduce["prompt"]


async def test_every_batch_failing_returns_an_empty_summary_rather_than_an_invention(
    monkeypatch,
):
    """Honest emptiness beats a confident summary derived from nothing."""

    async def always_fails(transcript, target_language, message_count, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(
        summary_module, "generate_conversation_summary", always_fails
    )

    count = MAP_BATCH_MESSAGES * 2
    result = await generate_long_conversation_summary(
        transcript_lines=_lines(count),
        target_language="en",
        message_count=count,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    assert result.summary == ""
    assert result.key_points == []
    assert result.message_count == count


async def test_one_surviving_batch_skips_the_reduce_call(monkeypatch):
    """A merge over a single input is a paraphrase — a chance to drift, for no gain."""
    calls = {"n": 0}

    async def one_survivor(transcript, target_language, message_count, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _response("only")
        raise RuntimeError("provider timeout")

    reduced = {"called": False}

    async def fake_invoke(**kwargs):
        reduced["called"] = True
        raise AssertionError("reduce must not run for a single partial")

    monkeypatch.setattr(summary_module, "generate_conversation_summary", one_survivor)
    monkeypatch.setattr(summary_module, "invoke_with_repair", fake_invoke)

    count = MAP_BATCH_MESSAGES * 2
    result = await generate_long_conversation_summary(
        transcript_lines=_lines(count),
        target_language="en",
        message_count=count,
        window_start_at=None,
        window_end_at=None,
        conversation_id="c1",
    )

    assert result.summary == "only"
    assert reduced["called"] is False
