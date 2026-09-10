"""Turning a conversation into rows in `assistant_chunks` (ADR-37, ADR-38).

The write side of the assistant's retrieval. `src/services/assistant/chunking.py` decides
*where* the boundaries go and knows nothing about the database; this module reads
the messages, applies one strategy, embeds the result and stores it.

Three properties are worth stating once here rather than at each call site.

**Public messages only.** A chunk can gather six messages, so a per-reader
visibility filter cannot be applied to one after the fact — half a chunk is not
something that can be returned. The alternative to filtering afterwards is
building a different index per reader, which multiplies the embedding cost by the
size of the group to protect a case the assistant does not need: its own private
replies are answers, not source material. So `public_only()` applies here, the
same call the translation agent's context window makes for the same reason.

**One index per (conversation, strategy, embedding model).** All three are in the
key because all three change what a vector means. Re-indexing replaces the rows
for exactly that combination and leaves the others alone, which is what lets five
chunkings of one conversation coexist while a sweep compares them.

**Never partially written.** A conversation is re-indexed inside one transaction:
the old rows go and the new ones arrive together, so a run that dies halfway
leaves the previous index intact rather than a half-built one that retrieves
plausibly and wrongly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.database import get_async_session_maker
from src.database.models import AssistantChunk, Message, User
from src.services.assistant.agent_consent import has_consent
from src.services.assistant.chunking import Chunk, SourceMessage, build_chunks
from src.services.messaging.message_visibility import public_only
from src.services.shared.embeddings import (
    assistant_embedding_settings,
    embed,
    embedding_model_name,
)

logger = logging.getLogger(__name__)

# Cosine similarity between neighbouring messages, needed only by
# `semantic_split`. Computed from message-level vectors that are thrown away
# afterwards: they exist to find boundaries, not to be searched, and storing them
# would put a second copy of every message in a table nothing queries.
_SIMILARITY_STRATEGY = "semantic_split"


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two vectors, 0.0 when either has no magnitude."""
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


async def load_source_messages(
    db: AsyncSession,
    *,
    conversation_id: str,
    after: datetime | None = None,
) -> list[SourceMessage]:
    """Read one conversation's indexable messages, oldest first.

    Deleted and empty messages are dropped rather than indexed as blanks: an
    empty chunk embeds to a vector near nothing in particular, which then ranks
    unpredictably against real content.

    Speaker names follow the convention `ConversationIntelligenceService` already
    uses — display name, then username, then the local part of the email — so a
    chunk and a summary refer to the same person by the same word.

    Args:
        db: Session to read through.
        conversation_id: Conversation to read.
        after: Only messages sent strictly later than this, for the incremental
            pass. ``None`` reads the whole conversation.
    """
    conditions = [
        Message.conversation_id == conversation_id,
        Message.deleted_at.is_(None),
        Message.original_text != "",
        public_only(),
    ]
    if after is not None:
        conditions.append(Message.created_at > after)

    rows = await db.execute(
        select(Message, User)
        .join(User, User.id == Message.sender_id)
        .where(*conditions)
        .order_by(Message.created_at, Message.id)
    )

    messages: list[SourceMessage] = []
    for message, user in rows.all():
        if not message.original_text.strip():
            continue
        messages.append(
            SourceMessage(
                id=message.id,
                sender_label=(
                    user.display_name or user.username or user.email.split("@")[0]
                ),
                text=message.original_text,
                created_at=message.created_at,
            )
        )
    return messages


async def _adjacent_similarity(
    messages: Sequence[SourceMessage], *, settings: Settings
) -> list[float]:
    """Similarity between each consecutive pair, for `semantic_split`.

    Returns a shorter list than expected when an embedding fails, which
    `chunk_by_semantic_split` treats as "no boundary here" — merging rather than
    splitting, so a provider outage degrades towards `turn_window` instead of
    towards the one-message baseline this whole exercise is trying to beat.
    """
    vectors: list[list[float] | None] = []
    for message in messages:
        vectors.append(await embed(message.text, settings=settings))

    similarities: list[float] = []
    for left, right in zip(vectors, vectors[1:], strict=False):
        if left is None or right is None:
            # Above any sane threshold: an unknown boundary is not a boundary.
            similarities.append(1.0)
        else:
            similarities.append(_cosine(left, right))
    return similarities


async def index_conversation(
    db: AsyncSession,
    *,
    conversation_id: str,
    strategy: str = "turn_window",
    settings: Settings | None = None,
    commit: bool = True,
    incremental: bool = False,
    **chunk_options,
) -> int:
    """(Re)build the chunk index for one conversation under one strategy.

    Raises rather than swallowing, unlike the read path. A failed write must be
    visible: retrieval degrading quietly is acceptable because the recent-message
    window still answers, but an index that silently failed to build looks
    exactly like a conversation nobody has talked in, and the first sign would be
    an assistant that cannot remember anything.

    Args:
        db: Session to write through. The caller owns the transaction unless
            ``commit`` is set.
        conversation_id: Conversation to index.
        strategy: One of `ASSISTANT_CHUNK_STRATEGIES`.
        settings: Configuration; defaults to the process settings.
        commit: Commit before returning. False lets a batch job group work.
        incremental: Index only messages newer than what is already indexed and
            append them, instead of rebuilding. This is what makes indexing
            affordable on the message path: a full rebuild of a two-thousand
            message thread every twenty messages would spend a hundred times the
            embedding budget to change a few chunks at the end. The cost is a
            seam — a turn that straddles the boundary is split across two passes
            — which a periodic full rebuild from `scripts/maintenance/backfill_assistant_chunks.py`
            removes.
        **chunk_options: Passed to the chunking strategy, so a sweep can vary
            window sizes without a second entry point.

    Returns:
        How many chunks were written. Zero means the conversation had no
        indexable messages, which is not an error.
    """
    settings = settings or get_settings()
    embedding_settings = assistant_embedding_settings(settings)
    model = embedding_model_name(embedding_settings)

    # Where an incremental pass resumes from, and how the appended chunks keep
    # numbering. Read before the messages so the two agree: a message arriving
    # between the two queries is picked up by the next pass rather than
    # half-indexed by this one.
    watermark: datetime | None = None
    next_index = 0
    if incremental:
        watermark = await db.scalar(
            select(func.max(AssistantChunk.ends_at)).where(
                AssistantChunk.conversation_id == conversation_id,
                AssistantChunk.strategy == strategy,
                AssistantChunk.embedding_model == model,
            )
        )
        next_index = (
            await db.scalar(
                select(func.max(AssistantChunk.chunk_index)).where(
                    AssistantChunk.conversation_id == conversation_id,
                    AssistantChunk.strategy == strategy,
                    AssistantChunk.embedding_model == model,
                )
            )
            or -1
        ) + 1

    messages = await load_source_messages(
        db, conversation_id=conversation_id, after=watermark
    )
    if not messages:
        return 0

    similarity: list[float] = []
    if strategy == _SIMILARITY_STRATEGY:
        similarity = await _adjacent_similarity(messages, settings=embedding_settings)

    chunks = build_chunks(
        strategy, messages, adjacent_similarity=similarity, **chunk_options
    )
    if not chunks:
        return 0

    rows = [
        AssistantChunk(
            conversation_id=conversation_id,
            strategy=strategy,
            chunk_index=next_index + chunk.index,
            chunk_text=chunk.text,
            message_ids=json.dumps(list(chunk.message_ids)),
            token_count=chunk.token_count,
            starts_at=chunk.starts_at,
            ends_at=chunk.ends_at,
            parent_index=chunk.parent_index,
            embedding=await _embed_chunk(
                chunk, strategy=strategy, settings=embedding_settings
            ),
            embedding_model=model,
        )
        for chunk in chunks
    ]

    if not incremental:
        # Delete and insert in one transaction: a run that dies between them
        # would otherwise leave the conversation with no index at all, which
        # reads to the assistant as a conversation nobody has talked in.
        await db.execute(
            delete(AssistantChunk).where(
                AssistantChunk.conversation_id == conversation_id,
                AssistantChunk.strategy == strategy,
                AssistantChunk.embedding_model == model,
            )
        )
    db.add_all(rows)
    if commit:
        await db.commit()
    else:
        await db.flush()

    logger.info(
        "Indexed conversation %s: %d chunks (%s), strategy=%s, model=%s",
        conversation_id,
        len(rows),
        "appended" if incremental else "rebuilt",
        strategy,
        model,
    )
    return len(rows)


async def _embed_chunk(
    chunk: Chunk, *, strategy: str, settings: Settings
) -> list[float] | None:
    """Embed one chunk, or None when nothing will ever search it.

    Under `parent_child` the parents exist to be *returned*, not to be found —
    `_searchable_rows` restricts that strategy's candidate pool to rows with a
    `parent_index`. Embedding a parent would therefore spend provider quota on a
    vector no query can reach, and on the XL corpus the parents are roughly a
    quarter of all rows.

    The strategy has to be passed in: a null `parent_index` means "is a parent"
    under `parent_child` and "has no parents at all" under the other four, and
    the row alone cannot tell those apart.
    """
    if strategy == "parent_child" and chunk.parent_index is None:
        return None
    return await embed(chunk.text, settings=settings)


# --- Keeping the index current -------------------------------------------

# Strong references to running tasks: asyncio keeps only a weak one and will
# garbage-collect a task nobody awaits, mid-statement. Same reason
# `message_memory` holds its own set.
_BACKGROUND_TASKS: set[asyncio.Task] = set()

# How many new messages before the tail is indexed again. Not one: an embedding
# call per message on the send path is the cost NFR-01 rules out for the
# translation agent, and there is no reason to pay it here either — the
# assistant is asked a question every few dozen messages at most, and a window
# that ends twenty messages ago still answers "what did we decide".
#
# The same shape as the cadence ADR-24 gives conversation-profile inference,
# for the same reason: work that improves with more data does not have to run
# on every message to be current enough.
REINDEX_EVERY = 20


def schedule_chunk_index(
    *,
    conversation_id: str,
    sender_id: str | None,
    session_factory: Callable[[], AsyncSession] | None = None,
    settings: Settings | None = None,
) -> None:
    """Fire and forget an incremental index of one conversation's tail.

    Returns immediately and may fail silently. The message it was triggered by
    has already reached its recipients, and an index that is twenty messages
    behind still answers most questions — neither is worth holding up a send
    for, and neither is worth an error the sender would see.

    Gated on the sender's `store_memory` consent, checked inside the task
    because it needs a session. Same permission that governs
    `message_memory`: agreeing to be remembered is one decision, not two.

    Args:
        conversation_id: Conversation whose tail to index.
        sender_id: Whose consent governs indexing this. ``None`` does nothing —
            there is no configuration flag that substitutes for a person having
            agreed.
        session_factory: Session source; defaults to the application's.
        settings: Configuration; defaults to the process settings.
    """
    if not conversation_id or sender_id is None:
        return

    # Never during a test run, the same guard and the same reason
    # `embed_with_model` applies to its local fallback. Indexing embeds every
    # chunk it builds, so leaving this on would put real provider calls behind
    # any test that happens to grant `store_memory` and send a message —
    # spending quota on assertions that are not about indexing, and making the
    # suite's runtime depend on a rate limit. Tests that do mean to exercise
    # indexing call `index_conversation` directly.
    if "pytest" in sys.modules:
        return

    task = asyncio.create_task(
        _index_tail(
            conversation_id=conversation_id,
            sender_id=sender_id,
            session_factory=session_factory or get_async_session_maker(),
            settings=settings or get_settings(),
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _index_tail(
    *,
    conversation_id: str,
    sender_id: str,
    session_factory: Callable[[], AsyncSession],
    settings: Settings,
) -> None:
    """Index the messages added since the last pass, if it is time to.

    Swallows every failure. This runs detached from any request, so an
    exception here reaches nobody; what it would do is leave a task object
    holding a traceback and, in the test suite, a session that outlives its
    schema.
    """
    try:
        async with session_factory() as session:
            if not await has_consent(session, sender_id, "store_memory"):
                return

            # The cadence gate. Counted rather than tracked in a column: the
            # count is one indexed aggregate, and a column would be a second
            # piece of state that can disagree with the rows it describes.
            total = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.deleted_at.is_(None),
                    public_only(),
                )
            )
            if not total:
                return

            indexed_any = await session.scalar(
                select(AssistantChunk.id)
                .where(AssistantChunk.conversation_id == conversation_id)
                .limit(1)
            )
            # Index on the first message so a brand-new conversation is not
            # invisible for its first twenty, then every REINDEX_EVERY after.
            if indexed_any is not None and total % REINDEX_EVERY != 0:
                return

            await index_conversation(
                session,
                conversation_id=conversation_id,
                settings=settings,
                incremental=indexed_any is not None,
            )
    except Exception:
        logger.warning(
            "Background chunk indexing failed for conversation %s",
            conversation_id,
            exc_info=True,
        )
