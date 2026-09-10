"""What the assistant remembers about a person, across conversations.

Distinct from `assistant_chunks`, which is a searchable copy of what was *said*.
This holds what was *learned*: that someone wants a day's notice, owns the
payments area, has a sync every Monday. None of that belongs to a conversation,
which is why `conversation_id` is nullable — a preference stated once in a group
is true everywhere afterwards.

Two rules give the table its shape.

**Nothing is overwritten.** A replacement fact points the old row at itself
through `superseded_by`. Overwriting would collapse "never said anything about
this" and "said otherwise and changed their mind" into the same absence, and the
second is precisely what decides whether to ask again. `agent_consents` keeps
revoked rows for the same reason.

**Everything here lives under `store_memory`.** Not checked in this module: the
tools that call it declare the scope, and `available_tools` filters on it before
the planner ever sees them. Putting the check here too would mean two places
that can disagree about what the permission covers.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.database.models import ASSISTANT_MEMORY_KINDS, AssistantUserMemory
from src.services.shared.embeddings import (
    assistant_embedding_settings,
    embed,
    embedding_model_name,
)

logger = logging.getLogger(__name__)

DEFAULT_RECALL_TOP_K = 3

# Above this cosine similarity, a new fact is treated as replacing an existing
# one rather than joining it. Deliberately high: merging two facts that only
# looked alike loses one of them permanently, while keeping a near-duplicate
# costs one row and a little context.
SUPERSEDE_SIMILARITY = 0.92


class MemoryKindError(ValueError):
    """The kind is not one the database will accept."""


async def remember(
    db: AsyncSession,
    *,
    user_id: str,
    kind: str,
    content: str,
    conversation_id: str | None = None,
    confidence: float = 0.5,
    source_message_id: str | None = None,
    settings: Settings | None = None,
    commit: bool = True,
) -> AssistantUserMemory:
    """Record one durable fact, superseding a close enough existing one.

    Raises rather than swallowing, unlike the recall path. A memory that failed
    to save looks exactly like a person who never mentioned the thing, and the
    assistant would go on asking them about it forever.

    Raises:
        MemoryKindError: `kind` is outside `ASSISTANT_MEMORY_KINDS`. Raised here
            rather than left to the CheckConstraint so the message names the
            vocabulary instead of the constraint.
    """
    if kind not in ASSISTANT_MEMORY_KINDS:
        raise MemoryKindError(
            f"Unknown memory kind {kind!r}. Expected one of: {', '.join(ASSISTANT_MEMORY_KINDS)}"
        )

    settings = assistant_embedding_settings(settings or get_settings())
    vector = await embed(content, settings=settings)

    fact = AssistantUserMemory(
        user_id=user_id,
        conversation_id=conversation_id,
        kind=kind,
        content=content.strip(),
        confidence=confidence,
        source_message_id=source_message_id,
        embedding=vector,
        embedding_model=embedding_model_name(settings) if vector else "",
    )
    db.add(fact)
    await db.flush()

    if vector is not None:
        await _supersede_near_duplicates(db, fact=fact, vector=vector, settings=settings)

    if commit:
        await db.commit()
    return fact


async def _supersede_near_duplicates(
    db: AsyncSession,
    *,
    fact: AssistantUserMemory,
    vector: list[float],
    settings: Settings,
) -> None:
    """Point older, near-identical facts of the same kind at the new one.

    Scoped to the same `kind` on purpose: "prefers a day's notice" and "owns the
    payments area" can be worded similarly enough to cross the threshold, and
    superseding across kinds would silently delete one of two unrelated things
    the assistant knows.
    """
    model = embedding_model_name(settings)
    rows = await db.scalars(
        select(AssistantUserMemory)
        .where(
            AssistantUserMemory.user_id == fact.user_id,
            AssistantUserMemory.kind == fact.kind,
            AssistantUserMemory.id != fact.id,
            AssistantUserMemory.superseded_by.is_(None),
            AssistantUserMemory.embedding_model == model,
            AssistantUserMemory.embedding.is_not(None),
            # pgvector's cosine *distance*, so the threshold inverts.
            AssistantUserMemory.embedding.cosine_distance(vector)
            < (1 - SUPERSEDE_SIMILARITY),
        )
    )
    for stale in rows.all():
        stale.superseded_by = fact.id


async def recall(
    db: AsyncSession,
    *,
    user_id: str,
    query_text: str,
    top_k: int = DEFAULT_RECALL_TOP_K,
    settings: Settings | None = None,
) -> list[AssistantUserMemory]:
    """Return the current facts closest in meaning to ``query_text``.

    Never raises, and never returns a superseded row. Scoped to one account with
    no way to widen it: memory is the most personal thing the assistant holds,
    and there is no feature that wants another person's.

    Ordered by distance, then by confidence descending. The second key matters
    because these are model inferences rather than statements: two facts equally
    close to the query are not equally believed, and the firmer one should reach
    the prompt first.
    """
    cleaned = (query_text or "").strip()
    if not cleaned or top_k < 1:
        return []

    settings = assistant_embedding_settings(settings or get_settings())
    try:
        vector = await embed(cleaned, settings=settings)
        if vector is None:
            return []

        rows = await db.scalars(
            select(AssistantUserMemory)
            .where(
                AssistantUserMemory.user_id == user_id,
                AssistantUserMemory.superseded_by.is_(None),
                AssistantUserMemory.embedding_model == embedding_model_name(settings),
                AssistantUserMemory.embedding.is_not(None),
            )
            .order_by(
                AssistantUserMemory.embedding.cosine_distance(vector),
                AssistantUserMemory.confidence.desc(),
            )
            .limit(top_k)
        )
        return list(rows.all())
    except Exception:
        logger.warning("Assistant memory recall failed", exc_info=True)
        return []
