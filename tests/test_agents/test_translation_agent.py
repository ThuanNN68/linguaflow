"""Tests for the Translation Agent (F-03.1).

Every test mocks the LLM instead of calling a real API: model output is not
deterministic, real calls are slow and cost money, and the suite would turn red
as soon as the free tier runs out.

Fixtures live in this file rather than tests/conftest.py to avoid conflicting
with feature/f-01-2-auth-user-config, which rewrites conftest.
"""


from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph import END

from src.agents.context_provider import InMemoryContextProvider, NullContextProvider
from src.agents.graph import (
    build_translation_graph,
    route_after_detect,
    route_after_validate,
)
from src.agents.nodes.translation import detect_language, validate_output
from src.services.language.fallback_translator import FALLBACK_MODEL_NAME

MODULE = "src.agents.nodes.translation"


def make_llm(*responses: str) -> AsyncMock:
    """Fake LLM returning the given contents, one per successive call."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        side_effect=[type("Msg", (), {"content": r})() for r in responses]
    )
    llm.model_name = "mock-model"
    return llm


# ============================================================
# Routing
# ============================================================


def test_route_selects_passthrough_when_languages_match():
    """Route to passthrough when source and target languages are identical."""
    state = {"source_language": "vi", "target_language": "vi"}
    assert route_after_detect(state) == "passthrough"


def test_route_selects_build_context_when_languages_differ():
    """Route to build_context when source and target languages differ."""
    state = {"source_language": "vi", "target_language": "en"}
    assert route_after_detect(state) == "build_context"


def test_route_selects_validate_output_when_detection_failed():
    """Route directly to validate_output when detection encountered an error."""
    state = {"error": "detect that bai", "source_language": "vi", "target_language": "en"}
    assert route_after_detect(state) == "validate_output"


def test_route_ends_when_translation_valid():
    """Route to END when translation succeeds without fallback."""
    assert route_after_validate({"is_fallback": False}) == END


def test_route_selects_secondary_when_fallback_flagged():
    """Route to fallback_translate when is_fallback is True."""
    assert route_after_validate({"is_fallback": True}) == "fallback_translate"


# ============================================================
# End-to-end flow
# ============================================================


@pytest.mark.asyncio
async def test_graph_skips_llm_when_languages_match():
    """Source equals target and langdetect agrees: no LLM call at all."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock()

    with (
        patch(f"{MODULE}._detect_local", return_value="vi"),
        patch(f"{MODULE}.get_llm", return_value=llm),
    ):
        graph = build_translation_graph(NullContextProvider())
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Chào bạn nhé",
                "source_language": "vi",
                "target_language": "vi",
            }
        )

    assert result["translated_text"] == "Chào bạn nhé"
    assert result["is_fallback"] is False
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_translates_when_languages_differ():
    """langdetect agrees with the sender, so only the translate call is spent."""
    llm = make_llm("Hello there")

    with (
        patch(f"{MODULE}._detect_local", return_value="vi"),
        patch(f"{MODULE}.get_llm", return_value=llm),
    ):
        graph = build_translation_graph(NullContextProvider())
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Chào bạn nhé",
                "source_language": "vi",
                "target_language": "en",
            }
        )

    assert result["translated_text"] == "Hello there"
    assert result["is_valid"] is True
    assert result["is_fallback"] is False
    assert result["model"] == "mock-model"
    assert llm.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_detection_overrides_provisional_source_language():
    """Incoming source_language is provisional; detection must override it
    (docs/api/contract.md section 4.3)."""
    llm = make_llm("en", "Xin chào")

    with patch(f"{MODULE}.get_llm", return_value=llm):
        graph = build_translation_graph(NullContextProvider())
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Hello",
                "source_language": "vi",  # provisional, and wrong
                "target_language": "vi",
            }
        )

    # Detected "en" differs from target "vi", so it must translate, not pass through
    assert result["source_language"] == "en"
    assert result["translated_text"] == "Xin chào"


@pytest.mark.asyncio
async def test_conversation_history_reaches_the_user_prompt():
    """Conversation history messages are formatted and included in translation prompt."""
    llm = make_llm("Did you deploy it yet?")
    provider = InMemoryContextProvider()
    provider.add_message("c1", "An: Tôi vừa merge PR rồi")
    provider.add_message("c1", "Bình: Ok để tôi review")

    with (
        patch(f"{MODULE}._detect_local", return_value="vi"),
        patch(f"{MODULE}.get_llm", return_value=llm),
    ):
        graph = build_translation_graph(provider)
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Deploy xong chưa?",
                "source_language": "vi",
                "target_language": "en",
            }
        )

    assert result["context_messages"] == [
        "An: Tôi vừa merge PR rồi",
        "Bình: Ok để tôi review",
    ]
    # Only one LLM call (translate), and its user prompt must carry the history
    user_prompt = llm.ainvoke.await_args_list[0].args[0][1]["content"]
    assert "Tôi vừa merge PR rồi" in user_prompt

    assert "Deploy xong chưa?" in user_prompt



# ============================================================
# detect_language — two-tier langdetect + LLM strategy
# ============================================================


@pytest.mark.asyncio
async def test_detect_skips_llm_when_langdetect_agrees():
    """Fast path: the user writes in the language they configured."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock()

    with (
        patch(f"{MODULE}._detect_local", return_value="vi"),
        patch(f"{MODULE}.get_llm", return_value=llm),
    ):
        result = await detect_language(
            {"original_text": "Chị xem giúp em phần WebSocket", "source_language": "vi"}
        )

    assert result["source_language"] == "vi"
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_calls_llm_when_langdetect_disagrees():
    """langdetect misfires (e.g. 'Ok anh' -> tl), so the LLM must arbitrate."""
    llm = make_llm("vi")

    with (
        patch(f"{MODULE}._detect_local", return_value="tl"),
        patch(f"{MODULE}.get_llm", return_value=llm),
    ):
        result = await detect_language(
            {"original_text": "Ok anh nhé em làm luôn", "source_language": "vi"}
        )

    assert result["source_language"] == "vi"
    assert llm.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_detect_calls_llm_when_langdetect_fails():
    """When local langdetect returns None, LLM is called to detect source language."""
    llm = make_llm("en")

    with (
        patch(f"{MODULE}._detect_local", return_value=None),
        patch(f"{MODULE}.get_llm", return_value=llm),
    ):
        result = await detect_language(
            {"original_text": "The API is ready", "source_language": "vi"}
        )

    assert result["source_language"] == "en"


@pytest.mark.asyncio
async def test_detect_keeps_provisional_when_both_tiers_fail():
    """When both local detection and LLM fail, keep the provisional source language."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("API down"))

    with (
        patch(f"{MODULE}._detect_local", return_value=None),
        patch(f"{MODULE}.get_llm", return_value=llm),
    ):
        result = await detect_language(
            {"original_text": "Một câu tiếng Việt", "source_language": "vi"}
        )

    assert result["source_language"] == "vi"


@pytest.mark.asyncio
async def test_detect_skipped_for_text_below_threshold():
    """Below the character threshold neither tier is reliable, so neither runs."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock()

    with patch(f"{MODULE}.get_llm", return_value=llm):
        result = await detect_language({"original_text": "Ok", "source_language": "vi"})

    assert result["source_language"] == "vi"
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_skipped_for_text_without_letters():
    """Text without letters skips detection and keeps the provisional language."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock()

    with patch(f"{MODULE}.get_llm", return_value=llm):
        result = await detect_language({"original_text": "👍👍👍", "source_language": "vi"})

    assert result["source_language"] == "vi"
    llm.ainvoke.assert_not_awaited()


# ============================================================
# Fallback — NFR-02: an LLM failure must never drop the message
# ============================================================


@pytest.mark.asyncio
async def test_returns_original_when_llm_and_secondary_both_fail():
    """Both providers down: the recipient still gets the untranslated message."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("API down"))

    with (
        patch(f"{MODULE}.get_llm", return_value=llm),
        patch(f"{MODULE}.translate_with_secondary_provider", AsyncMock(return_value=None)),
    ):
        graph = build_translation_graph(NullContextProvider())
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Chào bạn",
                "source_language": "vi",
                "target_language": "en",
            }
        )

    assert result["translated_text"] == "Chào bạn"
    assert result["is_fallback"] is True
    assert result["is_valid"] is False


@pytest.mark.asyncio
async def test_returns_original_when_llm_returns_blank():
    """Empty LLM output triggers fallback to the original message."""
    llm = make_llm("   ")

    with (
        patch(f"{MODULE}._detect_local", return_value="vi"),
        patch(f"{MODULE}.get_llm", return_value=llm),
        patch(f"{MODULE}.translate_with_secondary_provider", AsyncMock(return_value=None)),
    ):
        graph = build_translation_graph(NullContextProvider())
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Chào bạn nhé",
                "source_language": "vi",
                "target_language": "en",
            }
        )

    assert result["translated_text"] == "Chào bạn nhé"
    assert result["is_fallback"] is True


@pytest.mark.asyncio
async def test_translates_when_context_provider_fails():
    """Losing context degrades quality but must not block the flow."""

    class BrokenProvider:
        async def get_recent_messages(self, conversation_id, limit=5):
            raise ConnectionError("DB down")

    llm = make_llm("Hello")

    with (
        patch(f"{MODULE}._detect_local", return_value="vi"),
        patch(f"{MODULE}.get_llm", return_value=llm),
    ):
        graph = build_translation_graph(BrokenProvider())
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Chào bạn nhé",
                "source_language": "vi",
                "target_language": "en",
            }
        )

    assert result["translated_text"] == "Hello"
    assert result["is_fallback"] is False


@pytest.mark.asyncio
async def test_secondary_provider_translates_when_llm_fails():
    """ADR-07: the secondary provider translates when the LLM path fails."""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("API down"))

    with (
        patch(f"{MODULE}._detect_local", return_value="vi"),
        patch(f"{MODULE}.get_llm", return_value=llm),
        patch(
            f"{MODULE}.translate_with_secondary_provider",
            AsyncMock(return_value="Hello there"),
        ),
    ):
        graph = build_translation_graph(NullContextProvider())
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Chào bạn nhé",
                "source_language": "vi",
                "target_language": "en",
            }
        )

    assert result["translated_text"] == "Hello there"
    assert result["model"] == FALLBACK_MODEL_NAME
    # Still a degraded path, so the client keeps showing its fallback indicator
    assert result["is_fallback"] is True


@pytest.mark.asyncio
async def test_secondary_provider_skipped_on_successful_translation():
    """The secondary provider must stay off the happy path — it costs latency."""
    llm = make_llm("Hello there")
    fallback = AsyncMock(return_value="should not be used")

    with (
        patch(f"{MODULE}._detect_local", return_value="vi"),
        patch(f"{MODULE}.get_llm", return_value=llm),
        patch(f"{MODULE}.translate_with_secondary_provider", fallback),
    ):
        graph = build_translation_graph(NullContextProvider())
        result = await graph.ainvoke(
            {
                "conversation_id": "c1",
                "original_text": "Chào bạn nhé",
                "source_language": "vi",
                "target_language": "en",
            }
        )

    assert result["translated_text"] == "Hello there"
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_original_when_translation_too_long():
    """Guards the case where the LLM explains itself instead of translating."""
    state = {
        "original_text": "Chào",
        "translated_text": "Đây là bản dịch của bạn: " + "x" * 300,
    }
    result = await validate_output(state)

    assert result["is_fallback"] is True
    assert result["translated_text"] == "Chào"


@pytest.mark.asyncio
async def test_reports_error_when_original_text_empty():
    """Empty original text is recorded as an error and falls back safely."""
    llm = make_llm("vi")

    with (
        patch(f"{MODULE}.get_llm", return_value=llm),
        patch(f"{MODULE}.translate_with_secondary_provider", AsyncMock(return_value=None)),
    ):
        graph = build_translation_graph(NullContextProvider())
        result = await graph.ainvoke(
            {"conversation_id": "c1", "original_text": "", "target_language": "en"}
        )

    assert result["is_fallback"] is True
    assert "error" in result
