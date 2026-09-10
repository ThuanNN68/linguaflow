"""Remembering what a message meant, so context can be found by meaning.

`build_context` takes the last three to five messages. That is the right window
for resolving a pronoun and useless for a reference to something agreed forty
messages ago — the reader knows what "the migration we talked about" is, and the
model, given only the last four lines, does not.

This module writes the half that makes the other kind of retrieval possible: one
vector per message, stored beside it. Reading them back is
`DatabaseContextProvider`'s job.

Two things are deliberately true of the write path. It runs in the background,
because it is an API call and the message has already been delivered. And it
only runs when `RAG_CONTEXT_ENABLED` is on — embedding every message of every
conversation for a feature nobody has switched on would spend real quota on
nothing. The consequence, which is the honest cost of that choice: turning the
flag on starts the memory from that moment, and older messages stay invisible to
it until somebody backfills them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.database import get_async_session_maker
from src.database.models import MessageEmbedding
from src.services.assistant.agent_consent import has_consent
from src.services.shared.embeddings import embed_with_model

logger = logging.getLogger(__name__)

# Strong references to running tasks: asyncio keeps only a weak one and will
# garbage-collect a task nobody awaits, mid-statement.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def schedule_message_embedding(
    *,
    message_id: str,
    conversation_id: str,
    text: str,
    sender_id: str | None = None,
    session_factory: Callable[[], AsyncSession] | None = None,
    settings: Settings | None = None,
) -> None:
    """Fire and forget the embedding of one message.

    Returns immediately and can fail silently. The message it describes has
    already reached its recipients; nothing here may hold that up.

    Two independent reasons to remember a message, either of which suffices:
    `rag_context_enabled` serves the *translation* agent's semantic context
    (ADR-27), while the sender's `store_memory` consent serves the *assistant's*
    long-term memory. Turning the assistant on must not silently switch on RAG
    for translation, and vice versa, so neither is expressed in terms of the
    other. The consent half needs a session, so it is checked in `_store`.

    Args:
        message_id: Message to remember.
        conversation_id: Its conversation, denormalised onto the row so the
            nearest-neighbour search can be scoped without a join.
        text: The message body.
        sender_id: Whose consent governs remembering this message. ``None``
            falls back to the configuration flag alone.
        session_factory: Session source; defaults to the application's.
        settings: Configuration to read; defaults to the process settings.
    """
    settings = settings or get_settings()
    if not settings.rag_context_enabled and sender_id is None:
        return
    if not message_id or not conversation_id or not (text or "").strip():
        return

    task = asyncio.create_task(
        _store(
            message_id=message_id,
            conversation_id=conversation_id,
            text=text,
            sender_id=sender_id,
            session_factory=session_factory or get_async_session_maker(),
            settings=settings,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _store(
    *,
    message_id: str,
    conversation_id: str,
    text: str,
    session_factory: Callable[[], AsyncSession],
    settings: Settings,
    sender_id: str | None = None,
) -> None:
    """Embed the text and store it, replacing any vector already held.

    Replacing rather than skipping: a message can be edited, and a vector
    describing the wording it used to have would retrieve it for the wrong
    reasons. `embed` returns None on every failure, and None here simply means
    this message will not be found by meaning — the time-ordered window still
    covers it.
    """
    try:
        if not settings.rag_context_enabled:
            # Only reachable with a sender: the scheduler returns early
            # otherwise. Checked before embedding so a message nobody agreed to
            # remember is never sent to the embedding provider at all.
            async with session_factory() as session:
                if sender_id is None or not await has_consent(session, sender_id, "store_memory"):
                    return

        vector, model = await embed_with_model(text, settings=settings)
        if vector is None:
            return

        async with session_factory() as session:
            row = await session.scalar(
                select(MessageEmbedding).where(
                    MessageEmbedding.message_id == message_id
                )
            )
            if row is None:
                row = MessageEmbedding(
                    message_id=message_id, conversation_id=conversation_id
                )
                session.add(row)
            row.embedding = vector
            row.embedding_model = model
            await session.commit()
    except Exception as exc:
        logger.warning("Storing the embedding for a message failed: %s", exc)


def embedding_write_is_enabled(settings: Settings | None = None) -> bool:
    """Whether messages are being remembered at all.

    Exposed so a caller can say why retrieval found nothing, rather than
    leaving "no results" to mean both "nothing matched" and "nothing stored".
    """
    return (settings or get_settings()).rag_context_enabled
