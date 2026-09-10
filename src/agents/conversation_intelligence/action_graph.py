"""Candidate action extraction agent logic and graph (B-04)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.conversation_intelligence.observability import build_runnable_config
from src.agents.conversation_intelligence.parsing import invoke_with_repair
from src.agents.conversation_intelligence.prompts import (
    ACTION_EXTRACTION_SYSTEM_PROMPT,
    build_action_extraction_user_prompt,
)
from src.config import Settings, get_settings
from src.schemas.intelligence import ActionCandidateDTO, ActionExtractionPayload
from src.services.shared.llm import get_llm


async def extract_action_candidates(
    message_text: str,
    sender_id: str,
    sender_name: str,
    members: list[dict[str, str]],
    reference_timestamp: datetime | None,
    conversation_id: str,
    message_id: str,
    settings: Settings | None = None,
    provider: str | None = None,
) -> list[ActionCandidateDTO]:
    """Extract candidate action items from message text using LLM."""
    clean_text = message_text.strip()
    if not clean_text:
        return []

    settings = settings or get_settings()
    ref_time = reference_timestamp or datetime.now(UTC)
    ref_time_str = ref_time.isoformat()

    members_lines = [
        f"- {m.get('name', 'User')} (@{m.get('username', '')}) [id: {m.get('id', '')}]"
        for m in members
    ]
    members_context = "\n".join(members_lines) if members_lines else f"- {sender_name} [id: {sender_id}]"

    schema_json = json.dumps(ActionExtractionPayload.model_json_schema(), indent=2)
    system_prompt = ACTION_EXTRACTION_SYSTEM_PROMPT.format(
        reference_timestamp=ref_time_str,
        schema_json=schema_json,
    )
    user_prompt = build_action_extraction_user_prompt(
        message_text=clean_text,
        sender_id=sender_id,
        sender_name=sender_name,
        members_context=members_context,
        reference_timestamp=ref_time_str,
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    llm = get_llm(settings=settings, provider=provider)
    timeout = float(settings.llm_timeout_seconds)

    runnable_config = build_runnable_config(
        operation="extract_actions",
        conversation_id=conversation_id,
        message_id=message_id,
    )

    payload = await invoke_with_repair(
        llm=llm,
        messages=messages,
        schema=ActionExtractionPayload,
        operation="extract_actions",
        timeout=timeout,
        provider=provider or settings.llm_provider,
        model=settings.llm_model,
        conversation_id=conversation_id,
        message_id=message_id,
        runnable_config=runnable_config,
    )

    # Ownership is deliberately not accepted from the model.  The API/service
    # determines requester eligibility and supplies the persisted owner.
    sanitized_candidates: list[ActionCandidateDTO] = []
    for cand in payload.candidates:
        cand.owner_user_id = None
        sanitized_candidates.append(cand)

    return sanitized_candidates
