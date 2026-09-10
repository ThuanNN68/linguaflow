"""Robust structured JSON extraction, validation, and single-repair invocation (B-09)."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from src.agents.conversation_intelligence.errors import (
    IntelligenceError,
    IntelligenceErrorCode,
)
from src.agents.conversation_intelligence.observability import (
    log_intelligence_event,
)
from src.services.shared.llm import extract_text

T = TypeVar("T", bound=BaseModel)

_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _discard_cancelled_task(task: asyncio.Future[Any]) -> None:
    """Consume a detached provider task's terminal exception.

    Some SDKs (notably Gemini's async client) do not finish promptly after
    cancellation.  The API request must still be allowed to return its timeout
    response, while this callback prevents an unobserved-task warning later.
    """
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _await_with_hard_timeout(awaitable: Any, timeout: float) -> Any:
    """Return within ``timeout`` even when a provider ignores cancellation."""
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        task.add_done_callback(_discard_cancelled_task)
        raise TimeoutError
    return task.result()


def clean_json_text(raw_text: str) -> str:
    """Extract and normalize JSON text from raw LLM output.

    Handles:
    - Fenced code blocks (```json ... ``` or ``` ... ```)
    - Leading/trailing conversational filler by finding first '[' or '{' and matching closing bracket.
    """
    if not raw_text:
        return ""

    text = raw_text.strip()

    # Try matching fenced block
    fence_match = _JSON_FENCE_PATTERN.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # If text starts with non-JSON filler, find first '{' or '['
    first_obj = text.find("{")
    first_arr = text.find("[")

    if first_obj != -1 and (first_arr == -1 or first_obj < first_arr):
        last_obj = text.rfind("}")
        if last_obj != -1 and last_obj > first_obj:
            return text[first_obj : last_obj + 1].strip()
    elif first_arr != -1:
        last_arr = text.rfind("]")
        if last_arr != -1 and last_arr > first_arr:
            return text[first_arr : last_arr + 1].strip()

    return text


def parse_structured_json(
    raw_text: str,
    schema: type[T],
    operation: str = "parsing",
) -> T:
    """Parse raw text into a Pydantic model.

    Raises:
        IntelligenceError(INVALID_OUTPUT) on malformed JSON or validation failure.
    """
    cleaned = clean_json_text(raw_text)
    if not cleaned:
        raise IntelligenceError(
            IntelligenceErrorCode.INVALID_OUTPUT,
            "Model returned empty or non-JSON response",
            operation=operation,
        )

    try:
        parsed_data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise IntelligenceError(
            IntelligenceErrorCode.INVALID_OUTPUT,
            f"Failed to decode JSON: {exc.msg}",
            operation=operation,
            details={"error_type": "json_decode", "pos": exc.pos},
        ) from exc

    try:
        return schema.model_validate(parsed_data)
    except ValidationError as exc:
        raise IntelligenceError(
            IntelligenceErrorCode.INVALID_OUTPUT,
            f"JSON did not conform to schema {schema.__name__}: {exc.error_count()} errors",
            operation=operation,
            details={"error_type": "schema_validation", "errors": exc.errors()},
        ) from exc


async def invoke_with_repair(
    llm: Any,
    messages: list[BaseMessage],
    schema: type[T],
    operation: str = "intelligence_operation",
    timeout: float = 15.0,
    provider: str = "",
    model: str = "",
    conversation_id: str | None = None,
    message_id: str | None = None,
    repair_prompt_fn: Callable[[str, str], list[BaseMessage]] | None = None,
    runnable_config: dict[str, Any] | None = None,
) -> T:
    """Invoke LLM with bounded timeout, structured parsing, and exactly ONE format repair attempt.

    Invariants:
    - Provider call failure/timeout -> No second call; return safe error immediately.
    - Malformed JSON / schema mismatch -> Exactly one repair retry; if still failing, return safe error.
    - Telemetry is logged safely on all paths.
    """
    start_time = time.perf_counter()

    # 1. First invocation attempt
    try:
        response = await _await_with_hard_timeout(
            llm.ainvoke(messages, config=runnable_config) if runnable_config else llm.ainvoke(messages),
            timeout,
        )
    except TimeoutError as exc:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        log_intelligence_event(
            operation=operation,
            status="timeout",
            latency_ms=latency_ms,
            provider=provider,
            model=model,
            conversation_id=conversation_id,
            message_id=message_id,
            error_code=IntelligenceErrorCode.TIMEOUT.value,
        )
        raise IntelligenceError(
            IntelligenceErrorCode.TIMEOUT,
            f"Operation timed out after {timeout:.1f}s",
            operation=operation,
        ) from exc
    except Exception as exc:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        log_intelligence_event(
            operation=operation,
            status="provider_error",
            latency_ms=latency_ms,
            provider=provider,
            model=model,
            conversation_id=conversation_id,
            message_id=message_id,
            error_code=IntelligenceErrorCode.PROVIDER_UNAVAILABLE.value,
        )
        raise IntelligenceError(
            IntelligenceErrorCode.PROVIDER_UNAVAILABLE,
            "Underlying AI provider call failed",
            operation=operation,
        ) from exc

    raw_output = extract_text(response)

    first_error_msg = ""
    # 2. Try parsing first response
    try:
        result = parse_structured_json(raw_output, schema, operation=operation)
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        log_intelligence_event(
            operation=operation,
            status="success",
            latency_ms=latency_ms,
            provider=provider,
            model=model,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        return result
    except IntelligenceError as parse_err:
        # First output was malformed. Attempt ONE repair retry.
        first_error_msg = parse_err.message

    # 3. Exactly ONE repair retry
    repair_messages: list[BaseMessage]
    if repair_prompt_fn is not None:
        repair_messages = repair_prompt_fn(raw_output, first_error_msg)
    else:
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        repair_content = (
            "Your previous response was not valid JSON conforming to the required schema.\n"
            f"Error details: {first_error_msg}\n"
            f"Required JSON Schema:\n{schema_json}\n"
            "Output ONLY the valid JSON object with no explanations, markdown formatting, or code fences."
        )
        repair_messages = [
            *messages,
            HumanMessage(content=raw_output),
            SystemMessage(content=repair_content),
        ]

    try:
        repair_response = await _await_with_hard_timeout(
            llm.ainvoke(repair_messages, config=runnable_config)
            if runnable_config
            else llm.ainvoke(repair_messages),
            timeout,
        )
        repair_output = extract_text(repair_response)
        result = parse_structured_json(repair_output, schema, operation=operation)
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        log_intelligence_event(
            operation=operation,
            status="success",
            latency_ms=latency_ms,
            provider=provider,
            model=model,
            conversation_id=conversation_id,
            message_id=message_id,
            repaired=True,
        )
        return result
    except Exception as exc:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        log_intelligence_event(
            operation=operation,
            status="invalid_output",
            latency_ms=latency_ms,
            provider=provider,
            model=model,
            conversation_id=conversation_id,
            message_id=message_id,
            error_code=IntelligenceErrorCode.INVALID_OUTPUT.value,
        )
        raise IntelligenceError(
            IntelligenceErrorCode.INVALID_OUTPUT,
            "AI provider returned invalid structured output after repair attempt",
            operation=operation,
        ) from exc
