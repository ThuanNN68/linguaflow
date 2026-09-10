"""Summarization logic for on-demand conversation summary (B-03).

Two entry points. `generate_conversation_summary` is one prompt over one
transcript and remains what most conversations get. `generate_long_conversation_summary`
adds a map-reduce stage above `LONG_CONVERSATION_THRESHOLD` messages, because a
single pass over four hundred messages summarises the ends and loses the middle
(ADR-38) - and the assistant is expected to answer over threads of thousands.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.conversation_intelligence.observability import build_runnable_config
from src.agents.conversation_intelligence.parsing import invoke_with_repair
from src.agents.conversation_intelligence.prompts import (
    SUMMARY_REDUCE_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    build_summary_reduce_user_prompt,
    build_summary_user_prompt,
)
from src.config import Settings, get_settings
from src.schemas.intelligence import (
    ConversationSummaryPayload,
    ConversationSummaryResponse,
)
from src.services.shared.llm import get_llm

logger = logging.getLogger(__name__)


def format_transcript_line(created_at_str: str, speaker_name: str, text: str) -> str:
    """Format one message line for summarization grounding."""
    clean_text = text.replace("\r", " ").replace("\n", " ").strip()
    return f"[{created_at_str}] {speaker_name}: {clean_text}"


async def generate_conversation_summary(
    transcript: str,
    target_language: str,
    message_count: int,
    window_start_at: Any | None,
    window_end_at: Any | None,
    conversation_id: str,
    settings: Settings | None = None,
    provider: str | None = None,
) -> ConversationSummaryResponse:
    """Generate structured conversation summary with LLM grounding and repair retry."""
    settings = settings or get_settings()

    # Fast bypass for empty conversation
    if not transcript.strip() or message_count == 0:
        return ConversationSummaryResponse(
            summary="",
            key_points=[],
            decisions=[],
            open_items=[],
            message_count=0,
            target_language=target_language,
            window_start_at=None,
            window_end_at=None,
        )

    schema_json = json.dumps(ConversationSummaryPayload.model_json_schema(), indent=2)
    system_prompt = SUMMARY_SYSTEM_PROMPT.format(
        target_language=target_language,
        schema_json=schema_json,
    )
    user_prompt = build_summary_user_prompt(
        transcript=transcript,
        target_language=target_language,
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    llm = get_llm(settings=settings, provider=provider)
    timeout = float(settings.llm_timeout_seconds)

    runnable_config = build_runnable_config(
        operation="conversation_summary",
        conversation_id=conversation_id,
        target_language=target_language,
        message_count=message_count,
    )

    payload = await invoke_with_repair(
        llm=llm,
        messages=messages,
        schema=ConversationSummaryPayload,
        operation="conversation_summary",
        timeout=timeout,
        provider=provider or settings.llm_provider,
        model=settings.llm_model,
        conversation_id=conversation_id,
        runnable_config=runnable_config,
    )

    return ConversationSummaryResponse(
        summary=payload.summary,
        key_points=payload.key_points,
        decisions=payload.decisions,
        open_items=payload.open_items,
        message_count=message_count,
        target_language=target_language,
        window_start_at=window_start_at,
        window_end_at=window_end_at,
    )


# --- Long conversations (ADR-38) -------------------------------------------

# Above this many messages, one prompt stops being the right shape. The number
# is not a context-window limit — modern windows swallow far more — it is where
# a single-pass summary starts losing the middle of the conversation. A model
# given four hundred messages reliably summarises the first thirty and the last
# thirty; the map stage exists so every part of the thread gets a pass in which
# it is the only thing on the page.
LONG_CONVERSATION_THRESHOLD = 60

# Messages per map-stage batch, with the same reasoning: small enough that
# nothing in the batch is crowded out, large enough that a decision and its
# follow-up usually land together.
MAP_BATCH_MESSAGES = 40

# Concurrent map calls. Bounded because every provider on the free tiers this
# project uses rate-limits, and forty parallel calls turn a long summary into a
# 429 rather than into a fast summary.
MAP_CONCURRENCY = 4


def _batch_lines(lines: Sequence[str], size: int) -> list[list[str]]:
    """Split transcript lines into fixed batches, preserving order."""
    return [list(lines[start : start + size]) for start in range(0, len(lines), size)]


def _render_partial(partial: ConversationSummaryResponse) -> str:
    """Flatten one partial summary into the text the reduce stage reads.

    Rendered rather than passed as JSON so the reduce prompt's own schema
    instruction is the only JSON shape in play; a nested payload invites the
    model to echo the input structure instead of producing the output one.
    """
    sections = [partial.summary.strip()]
    for label, items in (
        ("Key points", partial.key_points),
        ("Decisions", partial.decisions),
        ("Open items", partial.open_items),
    ):
        if items:
            body = "\n".join(f"- {item}" for item in items)
            sections.append(f"{label}:\n{body}")
    return "\n\n".join(section for section in sections if section)


async def generate_long_conversation_summary(
    transcript_lines: Sequence[str],
    target_language: str,
    message_count: int,
    window_start_at: Any | None,
    window_end_at: Any | None,
    conversation_id: str,
    settings: Settings | None = None,
    provider: str | None = None,
) -> ConversationSummaryResponse:
    """Summarise a long conversation in two stages: map, then reduce.

    Takes the transcript as lines rather than as one string, because the batch
    boundaries have to fall between messages. Splitting a joined transcript by
    character count would cut through the middle of a message and hand the map
    stage a fragment whose speaker and timestamp are in the previous batch.

    A failed batch is dropped rather than fatal, and the reduce stage runs on
    what survived. Losing one batch of forty messages costs part of a summary;
    raising would cost all of it, and the assistant's hard rule is that a bad
    model day must not become a broken feature.

    Falls back to the single-pass path when there are few enough messages, so
    there is one entry point rather than two behaviours the caller has to choose
    between.
    """
    settings = settings or get_settings()

    if len(transcript_lines) <= LONG_CONVERSATION_THRESHOLD:
        return await generate_conversation_summary(
            transcript="\n".join(transcript_lines),
            target_language=target_language,
            message_count=message_count,
            window_start_at=window_start_at,
            window_end_at=window_end_at,
            conversation_id=conversation_id,
            settings=settings,
            provider=provider,
        )

    batches = _batch_lines(transcript_lines, MAP_BATCH_MESSAGES)
    semaphore = asyncio.Semaphore(MAP_CONCURRENCY)

    async def summarise_batch(index: int, lines: list[str]):
        """One map call. Never raises; a dead batch returns None."""
        async with semaphore:
            try:
                return await generate_conversation_summary(
                    transcript="\n".join(lines),
                    target_language=target_language,
                    message_count=len(lines),
                    window_start_at=None,
                    window_end_at=None,
                    conversation_id=conversation_id,
                    settings=settings,
                    provider=provider,
                )
            except Exception:
                logger.warning(
                    "Map stage failed for batch %d of conversation %s; it will be "
                    "left out of the summary.",
                    index,
                    conversation_id,
                    exc_info=True,
                )
                return None

    results = await asyncio.gather(
        *(summarise_batch(index, lines) for index, lines in enumerate(batches))
    )
    partials = [_render_partial(result) for result in results if result is not None]

    if not partials:
        # Every batch failed. Returning an empty summary is honest; inventing one
        # from nothing is the failure mode this whole prompt chain guards against.
        return ConversationSummaryResponse(
            summary="",
            key_points=[],
            decisions=[],
            open_items=[],
            message_count=message_count,
            target_language=target_language,
            window_start_at=window_start_at,
            window_end_at=window_end_at,
        )

    if len(partials) == 1:
        # One surviving batch needs no merge, and a reduce call over a single
        # input is a paraphrase — a second chance to drift, for no gain.
        only = next(result for result in results if result is not None)
        return ConversationSummaryResponse(
            summary=only.summary,
            key_points=only.key_points,
            decisions=only.decisions,
            open_items=only.open_items,
            message_count=message_count,
            target_language=target_language,
            window_start_at=window_start_at,
            window_end_at=window_end_at,
        )

    schema_json = json.dumps(ConversationSummaryPayload.model_json_schema(), indent=2)
    messages = [
        SystemMessage(
            content=SUMMARY_REDUCE_SYSTEM_PROMPT.format(
                target_language=target_language,
                schema_json=schema_json,
            )
        ),
        HumanMessage(
            content=build_summary_reduce_user_prompt(partials, target_language)
        ),
    ]

    payload = await invoke_with_repair(
        llm=get_llm(settings=settings, provider=provider),
        messages=messages,
        schema=ConversationSummaryPayload,
        operation="conversation_summary_reduce",
        timeout=float(settings.llm_timeout_seconds),
        provider=provider or settings.llm_provider,
        model=settings.llm_model,
        conversation_id=conversation_id,
        runnable_config=build_runnable_config(
            operation="conversation_summary_reduce",
            conversation_id=conversation_id,
            target_language=target_language,
            message_count=message_count,
            partials=len(partials),
        ),
    )

    return ConversationSummaryResponse(
        summary=payload.summary,
        key_points=payload.key_points,
        decisions=payload.decisions,
        open_items=payload.open_items,
        message_count=message_count,
        target_language=target_language,
        window_start_at=window_start_at,
        window_end_at=window_end_at,
    )
