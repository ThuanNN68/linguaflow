"""Detached proactive commitment detection with an independent database session."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.database import get_async_session_maker
from src.services.assistant.agent_consent import has_consent
from src.services.intelligence.conversation_intelligence import ConversationIntelligenceService

logger = logging.getLogger(__name__)
_TASKS: set[asyncio.Task[Any]] = set()

def schedule_commitment_detection(*, message_id: str, conversation_id: str, sender_id: str, publisher: Any) -> None:
    task = asyncio.create_task(_detect(message_id, conversation_id, sender_id, publisher))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    task.add_done_callback(_log_failure)

def _log_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Background commitment detection failed", exc_info=task.exception())

async def _detect(message_id: str, conversation_id: str, sender_id: str, publisher: Any) -> None:
    async with get_async_session_maker()() as session:
        # Scanning every message as it arrives is the one thing the user did not
        # ask for, so it needs its own permission on top of `read_conversations`.
        # Checked with `has_consent` rather than `require_consent`: this runs
        # detached from the send path, and a missing permission is a normal
        # state to stop in, not a failure worth logging.
        if not await has_consent(session, sender_id, "proactive_scan"):
            return
        service = ConversationIntelligenceService()
        proposals = await service.detect_self_commitments_from_message(
            conversation_id=conversation_id, message_id=message_id, user_id=sender_id, db=session
        )
    for proposal in proposals:
        try:
            await publisher.send_to_user(sender_id, {"type": "action_proposal_created", "proposal": proposal.model_dump(mode="json")})
        except Exception:
            logger.warning("Action proposal event publish failed", exc_info=True)
