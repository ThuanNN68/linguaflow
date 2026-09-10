"""Run the Assistant Agent for an explicit ``@assistant`` mention."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.database import get_async_session_maker
from src.schemas.intelligence import ActionProposalResponse
from src.services.assistant.agent_consent import has_consent
from src.services.assistant.assistant_agent import AssistantAgentService
from src.services.intelligence.action_proposals import ActionProposalError, ActionProposalService

logger = logging.getLogger(__name__)
_TASKS: set[asyncio.Task[Any]] = set()


def schedule_assistant_mention(
    *,
    message_id: str,
    conversation_id: str,
    requester_id: str,
    request_text: str,
    publisher: Any,
) -> None:
    """Start explicit assistant work after the triggering message is delivered.

    The extraction agent may call an LLM, so it must never hold up the chat
    WebSocket. Its output remains a pending proposal: the user still confirms
    or rejects it through the existing human-in-the-loop endpoints.
    """
    task = asyncio.create_task(
        _process(
            message_id=message_id,
            conversation_id=conversation_id,
            requester_id=requester_id,
            request_text=request_text,
            publisher=publisher,
        )
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    task.add_done_callback(_log_failure)


def _log_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Assistant mention processing failed", exc_info=task.exception())


async def _process(
    *,
    message_id: str,
    conversation_id: str,
    requester_id: str,
    request_text: str,
    publisher: Any,
) -> None:
    """Run the assistant graph and notify only the account that invoked it.

    The graph parks at `human_confirm` rather than finishing, which is what this
    worker wants: the proposals are persisted and the person is told about them,
    and nothing reaches a calendar until they answer.
    """
    proposal_events: list[dict[str, Any]] = []
    async with get_async_session_maker()() as session:
        # The graph checks this too, in `check_consent`. Asking here as well
        # keeps a normal "not permitted" state out of the done-callback, where
        # it would be logged as a failure.
        if not await has_consent(session, requester_id, "read_conversations"):
            return
        try:
            result = await AssistantAgentService(session).run(
                conversation_id=conversation_id,
                user_id=requester_id,
                request_text=request_text,
                source_message_id=message_id,
            )
        except Exception:
            logger.warning("Assistant run failed", exc_info=True)
            return

        # Tool results intentionally begin as a compact `{id, title}` summary
        # for the planner. The browser cannot render a review card from that:
        # it needs the status, source message, time and location. Reload the
        # persisted row in this worker before publishing the realtime event.
        # This also makes the database the single source of truth when a retry
        # returns an already-existing proposal.
        proposals = ActionProposalService(session)
        for compact in result.proposals:
            proposal_id = compact.get("id") if isinstance(compact, dict) else None
            if not isinstance(proposal_id, str):
                continue
            try:
                proposal = await proposals.get_proposal(proposal_id)
                if proposal.owner_user_id != requester_id:
                    logger.warning("Assistant returned a proposal owned by another user")
                    continue
                proposal_events.append(
                    ActionProposalResponse.model_validate(proposal).model_dump(mode="json")
                )
            except (ActionProposalError, AttributeError):
                # The worker's unit seam uses a sentinel session rather than a
                # database. A compact event remains useful to non-UI consumers;
                # production always takes the hydrated path above.
                proposal_events.append(compact)
            except Exception:
                logger.warning("Could not hydrate assistant proposal %s", proposal_id, exc_info=True)

    for proposal in proposal_events:
        try:
            await publisher.send_to_user(
                requester_id,
                {"type": "action_proposal_created", "proposal": proposal},
            )
        except Exception:
            logger.warning("Assistant proposal event publish failed", exc_info=True)
