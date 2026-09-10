"""Database-backed conversation context for the Translation Agent (ADR-01).

Lives in the services layer, not in `src/agents/`, so the agent package never
imports `src/database/`. This class satisfies the `ContextProvider` protocol
structurally — the agent depends on the shape, not on this module.

Context lines are read from `messages.original_text`. Per ADR-01 the `messages`
table is the single source of context; there is no separate store to fall out of
sync with it.

Withdrawn messages are excluded. `original_text` survives a withdrawal in the
database, and the API blanks it out on the way to the client
(`src/api/routes.py`, `src/services/messaging/chat.py`); the agent has to honour the same
boundary, or a sender who withdrew a message would still see its wording reach
the model — and, through the model, a recipient. **That exclusion applies to
both retrieval paths.** A second way into the same table is a second way for a
withdrawn message to come back, and it would be the harder one to notice.

Since ADR-27 there are two paths. The window of recent messages answers "what
were we just saying", which is what resolves a pronoun. It cannot answer "what
did we decide about the migration", because that was forty messages ago; the
nearest neighbours by meaning can. They are merged into one list in time order,
indistinguishable to the model, because a line's provenance is not something the
translation should reason about.

The second path is off unless `RAG_CONTEXT_ENABLED` is on.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.context_provider import DEFAULT_CONTEXT_SIZE
from src.config import Settings, get_settings
from src.database.models import Message, MessageEmbedding
from src.services.messaging.message_visibility import public_only

logger = logging.getLogger(__name__)


class DatabaseContextProvider:
    """Reads the most recent messages of a conversation from the database.

    Args:
        session: Session to read through. The caller owns its lifetime; this
            class never commits.
        before_message_id: The message currently being translated. It is
            excluded from its own context — without this the model sees the
            sentence twice and tends to treat the repetition as emphasis.
    """

    def __init__(
        self,
        session: AsyncSession,
        before_message_id: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._before_message_id = before_message_id
        self._settings = settings or get_settings()

    async def get_recent_messages(
        self,
        conversation_id: str,
        limit: int = DEFAULT_CONTEXT_SIZE,
    ) -> list[str]:
        """Return up to `limit` recent messages, oldest first.

        Args:
            conversation_id: Conversation whose history to read.
            limit: Maximum number of messages to return.

        With `RAG_CONTEXT_ENABLED` on, the newest `limit` messages are joined
        by up to `RAG_TOP_K` older ones whose meaning is closest to the message
        being translated. `limit` bounds the time-ordered window only: the point
        of the second path is to reach past that window, so capping the total
        would simply push out the recent lines that resolve pronouns.

        Returns:
            Lines formatted as ``U01: text``, oldest first. Withdrawn and
            text-free messages are left out of both paths. Empty when the
            conversation has no other messages, or on any read failure —
            missing context degrades the translation but must not block it.
        """
        if not conversation_id or limit <= 0:
            return []

        # Newest-first with a limit, then reversed: the index on
        # (conversation_id, created_at, id) makes this a range scan rather than
        # a sort over the whole conversation.
        query = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                # Withdrawn: nobody can read it in the app any more, so nothing
                # of it may reach the model either.
                Message.deleted_at.is_(None),
                # Same reasoning, one step further: this context is built once
                # and shapes a translation delivered to every member, so a
                # message kept from some of them must not influence it.
                public_only(),
                # Attachment-only messages carry no text; an empty line would
                # cost tokens and tell the model nothing.
                Message.original_text != "",
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        if self._before_message_id is not None:
            query = query.where(Message.id != self._before_message_id)

        try:
            rows = list((await self._session.scalars(query)).all())
        except Exception as exc:
            logger.warning("Reading conversation context failed: %s", exc)
            return []

        remembered: list[Message] = []
        if self._settings.rag_context_enabled:
            remembered = await self._nearest_by_meaning(
                conversation_id=conversation_id,
                exclude_ids={row.id for row in rows},
            )

        # Merged and sorted as one history. The model is told the block is
        # oldest-first and nothing else about it; which path a line arrived by
        # is not something a translation should be reasoning about.
        merged = sorted(
            [*rows, *remembered], key=lambda row: (row.created_at, row.id)
        )
        return _format_context_lines(merged)

    async def _nearest_by_meaning(
        self,
        *,
        conversation_id: str,
        exclude_ids: set[str],
    ) -> list[Message]:
        """Find older messages closest in meaning to the one being translated.

        The query vector is the stored embedding of the message itself, so
        nothing is embedded here: this sits on the request path, and the vector
        was written in the background when the message arrived.

        Scoped to the conversation, which is not an optimisation. Retrieval
        reaching across conversations would take text from a thread the reader
        was never part of and put it in front of the model — the exact leak
        ADR-21 exists to catch on the way out, introduced on the way in.

        Returns an empty list on any failure, and whenever the message has no
        stored vector — which is every message sent before the flag was turned
        on.
        """
        if self._before_message_id is None:
            return []

        try:
            vector = await self._session.scalar(
                select(MessageEmbedding.embedding).where(
                    MessageEmbedding.message_id == self._before_message_id
                )
            )
            if vector is None:
                return []

            query = (
                select(Message)
                .join(MessageEmbedding, MessageEmbedding.message_id == Message.id)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.deleted_at.is_(None),
                    Message.original_text != "",
                    Message.id != self._before_message_id,
                    MessageEmbedding.embedding.is_not(None),
                    public_only(),
                )
                .order_by(MessageEmbedding.embedding.cosine_distance(vector))
                .limit(self._settings.rag_top_k + len(exclude_ids))
            )
            candidates = (await self._session.scalars(query)).all()
        except Exception as exc:
            logger.warning("Retrieving context by meaning failed: %s", exc)
            return []

        # Excluded after the query rather than inside it: the recent window is
        # usually among the nearest neighbours too, and a NOT IN over it would
        # have to be re-planned for every message. Over-fetching by its size and
        # filtering here costs one comparison per row.
        found = [row for row in candidates if row.id not in exclude_ids]
        chosen = found[: self._settings.rag_top_k]
        if chosen:
            # Count only, never content: server logs are read by people the
            # conversation never included.
            logger.info(
                "Context: %d recent lines joined by %d recalled by meaning",
                len(exclude_ids),
                len(chosen),
            )
        return chosen


def _format_context_lines(messages) -> list[str]:
    """Label each line with a per-conversation speaker alias.

    Speakers are numbered ``U01``, ``U02``, … in order of first appearance
    rather than named. Two reasons: the sender's row is not loaded here, so a
    name would cost a join per line; and the agent is meant to infer register
    and pronouns from what was said, not from who is labelled how. The same
    convention is used in `eval/golden_set.jsonl`, so what the evaluation
    measures is what production sends.
    """
    aliases: dict[str, str] = {}
    lines = []
    for message in messages:
        alias = aliases.get(message.sender_id)
        if alias is None:
            alias = f"U{len(aliases) + 1:02d}"
            aliases[message.sender_id] = alias
        lines.append(f"{alias}: {message.original_text}")
    return lines
