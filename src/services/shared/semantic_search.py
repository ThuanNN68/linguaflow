"""Find messages by meaning rather than by clock.

`DatabaseContextProvider._nearest_by_meaning` already searches the same index,
but it cannot be reused here and the reason is structural rather than a matter
of access: its query vector is *the stored embedding of one already-persisted
message*, so it answers "what is near this message" and can never answer "what
is near this question". The assistant needs the second one — a person asking
"what did we decide about the deadline" has written a sentence that is not in
the conversation at all.

So this embeds the query text, which the translation path deliberately never
does: that one runs on the request path and must not spend a network round trip
per message. This one runs when a person asked the assistant something and is
already waiting on a model, so one embedding call is affordable.

Scoping to a single conversation is not an optimisation. Retrieval reaching
across conversations would put text from a thread the reader was never part of
in front of the model — the leak ADR-21 catches on the way out, introduced on
the way in.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.database.models import Message, MessageEmbedding
from src.services.messaging.message_visibility import visible_to
from src.services.shared.embeddings import embed

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5


async def search_similar_messages(
    db: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    query_text: str,
    top_k: int = DEFAULT_TOP_K,
    settings: Settings | None = None,
) -> list[Message]:
    """Return the messages closest in meaning to ``query_text``.

    Returns `Message` rows rather than formatted strings, unlike
    `get_recent_messages`: the caller builds a transcript and needs the sender
    and the timestamp to do it, and re-fetching them afterwards would be a
    second query for data this one already read.

    Never raises. An embedding provider that is down, out of quota, or returning
    the wrong width means the assistant falls back to the recent-messages window
    it would have had anyway — a worse answer, not a failed request.

    Args:
        db: Session to read through.
        conversation_id: Conversation to search. Never widened.
        user_id: The authenticated caller. Applied as a visibility filter so
            this cannot become a second way to reach a private message.
        query_text: What to search for. Embedded here, so it may be a question
            that appears nowhere in the conversation.
        top_k: How many messages to return.
        settings: Configuration; defaults to the process settings.

    Returns:
        Messages ordered nearest first. Empty when nothing is stored, when the
        embedding failed, or when the conversation holds no vectors yet — which
        is every message sent before memory was switched on for its author.
    """
    text = (query_text or "").strip()
    if not text or top_k < 1:
        return []

    try:
        vector = await embed(text, settings=settings or get_settings())
        if vector is None:
            return []

        rows = await db.scalars(
            select(Message)
            .join(MessageEmbedding, MessageEmbedding.message_id == Message.id)
            .where(
                Message.conversation_id == conversation_id,
                Message.deleted_at.is_(None),
                Message.original_text != "",
                MessageEmbedding.embedding.is_not(None),
                visible_to(user_id),
            )
            .order_by(MessageEmbedding.embedding.cosine_distance(vector))
            .limit(top_k)
        )
        return list(rows.all())
    except Exception:
        logger.warning("Semantic message search failed", exc_info=True)
        return []
