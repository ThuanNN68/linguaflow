"""Tests for the measurements the agent records about its own run.

Telemetry is a separate concern from the translation itself: every assertion
here is about what was measured, never about what the recipient reads. The
`telemetry` dict is deliberately outside the product contract, so these tests
are the only thing pinning its keys down.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.context_provider import InMemoryContextProvider
from src.agents.graph import build_translation_graph
from src.agents.nodes.translation import detect_language, translate, validate_output

MODULE = "src.agents.nodes.translation"


def make_llm(*responses: AIMessage) -> AsyncMock:
    """Fake LLM returning full messages, so metadata is present to be read."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=list(responses))
    llm.model_name = "configured-model"
    return llm


def translated(text: str, *, model: str = "served-model", tokens: int = 10) -> AIMessage:
    """A response carrying the metadata a real provider would return."""
    return AIMessage(
        content=text,
        response_metadata={"model_name": model, "finish_reason": "stop"},
        usage_metadata={
            "input_tokens": tokens,
            "output_tokens": tokens,
            "total_tokens": tokens * 2,
        },
    )


@pytest.mark.asyncio
async def test_agreeing_detection_records_no_llm_call():
    """The fast path of ADR-11 must be visible as such in the numbers.

    This is the measurement that decides whether the two-tier design is worth
    keeping: if `detect_method` were rarely "langdetect", the local tier would
    be adding a step without removing a round trip.
    """
    state = {"original_text": "Cuộc họp dời sang chiều mai nhé", "source_language": "vi"}

    telemetry = (await detect_language(state))["telemetry"]

    assert telemetry["detect_method"] == "langdetect"
    assert telemetry["langdetect_agreed"] is True
    assert "llm_calls" not in telemetry


@pytest.mark.asyncio
async def test_arbitrating_detection_records_its_cost():
    """The second round trip is invisible in latency_ms alone; count it here."""
    state = {"original_text": "The deployment window moved to tomorrow", "source_language": "vi"}
    llm = make_llm(translated("en", tokens=30))

    with patch(f"{MODULE}.get_llm", return_value=llm):
        telemetry = (await detect_language(state))["telemetry"]

    assert telemetry["detect_method"] == "llm"
    assert telemetry["langdetect_agreed"] is False
    assert telemetry["llm_calls"] == 1
    assert telemetry["input_tokens"] == 30


@pytest.mark.asyncio
async def test_translation_records_the_model_the_provider_served():
    """The configured model and the served model are two different facts.

    `state["model"]` reports what was asked for, which is what the recipient's
    translation row records. The telemetry reports what answered.
    """
    state = {
        "original_text": "Bản build mới đã lên staging",
        "source_language": "vi",
        "target_language": "en",
    }
    llm = make_llm(translated("The new build is on staging", model="llama-3.3-70b-versatile"))

    with patch(f"{MODULE}.get_llm", return_value=llm):
        update = await translate(state)

    assert update["model"] == "configured-model"
    assert update["telemetry"]["model_served"] == "llama-3.3-70b-versatile"
    assert update["telemetry"]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_token_counts_accumulate_across_both_calls():
    """A run that arbitrates costs two calls, and the report must show both."""
    state = {
        "original_text": "The deployment window moved to tomorrow",
        "source_language": "vi",
        "target_language": "fr",
    }
    llm = make_llm(translated("en", tokens=30), translated("La fenêtre...", tokens=100))

    with patch(f"{MODULE}.get_llm", return_value=llm):
        after_detect = await detect_language(state)
        merged = {**state, **after_detect}
        after_translate = await translate(merged)

    telemetry = after_translate["telemetry"]
    assert telemetry["llm_calls"] == 2
    assert telemetry["input_tokens"] == 130
    assert telemetry["output_tokens"] == 130


@pytest.mark.asyncio
async def test_an_llm_failure_is_recorded_as_a_reason_code():
    """`fallback_reason` is a code so it can be counted; the log keeps the prose."""
    state = {
        "original_text": "Bản build mới đã lên staging",
        "source_language": "vi",
        "target_language": "en",
    }
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("503 Service Unavailable"))

    with patch(f"{MODULE}.get_llm", return_value=llm):
        update = await translate(state)

    assert update["telemetry"]["fallback_reason"] == "llm_error"
    assert update["telemetry"]["llm_calls"] == 1


@pytest.mark.asyncio
async def test_validation_keeps_the_reason_the_failing_node_recorded():
    """The node that failed knows why better than the one that notices."""
    state = {
        "original_text": "x" * 5000,
        "source_language": "vi",
        "target_language": "en",
        "error": "original_text exceeds the guardrail length limit",
        "telemetry": {"fallback_reason": "oversized_input"},
    }

    update = await validate_output(state)

    assert update["telemetry"]["fallback_reason"] == "oversized_input"
    assert update["telemetry"]["outcome"] == "original"


@pytest.mark.asyncio
async def test_an_overlong_translation_is_recorded_as_too_long():
    """Validation's own rejections carry their own code."""
    state = {
        "original_text": "Ok",
        "source_language": "vi",
        "target_language": "en",
        "translated_text": "This means okay, and here is a long explanation " * 20,
    }

    update = await validate_output(state)

    assert update["telemetry"]["fallback_reason"] == "too_long"


@pytest.mark.asyncio
async def test_a_successful_run_reports_the_llm_outcome():
    """End to end through the graph: the outcome must survive the merges."""
    graph = build_translation_graph(InMemoryContextProvider({"c1": ["U01: Chào team"]}))
    llm = make_llm(translated("The new build is on staging"))

    with patch(f"{MODULE}.get_llm", return_value=llm):
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Bản build mới đã lên staging",
                "source_language": "vi",
                "target_language": "en",
            }
        )

    telemetry = result["telemetry"]
    assert telemetry["outcome"] == "llm"
    assert telemetry["detect_method"] == "langdetect"
    assert telemetry["context_lines"] == 1
    assert telemetry["translate_ms"] >= 0
    assert "fallback_reason" not in telemetry


@pytest.mark.asyncio
async def test_a_passthrough_run_reports_no_model_work():
    """Same source and target must be countable, not just absent from failures.

    Without a row of its own a passthrough is indistinguishable from a message
    that was never translated at all, and it would quietly deflate the fallback
    rate by inflating the denominator with work nobody did.
    """
    graph = build_translation_graph()

    result = await graph.ainvoke(
        {
            "original_text": "Bản build mới đã lên staging",
            "source_language": "vi",
            "target_language": "vi",
        }
    )

    assert result["telemetry"]["outcome"] == "passthrough"
    assert result["telemetry"].get("llm_calls", 0) == 0
