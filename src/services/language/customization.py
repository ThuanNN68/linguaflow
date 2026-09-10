"""Database-backed source of the audience facts a translation is shaped by.

Satisfies `src/agents/customization.py`'s Protocol structurally rather than by
inheritance, the same arrangement `DatabaseContextProvider` uses: the agent
depends on the shape, this module depends on the schema, and neither imports
the other. That is what keeps the agent runnable against a different store, and
what lets the evaluation harness supply its own without a database at all.

Reads only. The rows come from the background inference that fills
`conversation_profiles`, and this sits on the request path where an extra model
call is not affordable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.customization import Customization
from src.database.models import ConversationProfile, Message
from src.services.language.glossary import lookup_terms

logger = logging.getLogger(__name__)


class DatabaseCustomizationProvider:
    """Looks up what a conversation is about and who it is with."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind one open session.

        Built per request, like `DatabaseContextProvider`: an AsyncSession is
        not safe for concurrent use, and the buckets of one message run under
        `asyncio.gather`.
        """
        self._session = session

    async def get_customization(
        self,
        conversation_id: str,
        *,
        original_text: str,
        source_language: str,
        target_language: str,
    ) -> Customization:
        """Return the conversation's inferred subject area and audience.

        The glossary lookup is wrapped separately from the profile read so a
        glossary problem costs only the terms: a conversation keeps its inferred
        audience even when no term can be resolved.

        Any failure returns the empty customization rather than raising. The
        caller is a graph node that must not fail a translation, and an absent
        profile is in any case indistinguishable from one that has not been
        inferred yet — which is every conversation for its first few messages.
        """
        if not conversation_id:
            return Customization()

        try:
            row = await self._session.scalar(
                select(ConversationProfile).where(
                    ConversationProfile.conversation_id == conversation_id
                )
            )
        except Exception as exc:
            logger.warning("Reading the conversation profile failed: %s", exc)
            return Customization()

        domain = row.domain if row is not None else ""
        audience = row.audience if row is not None else ""

        # Looked up after the profile, and with it: which rendering of a term
        # applies depends on who is reading, so "UI" resolves one way for an
        # internal audience and another for a client (ADR-26).
        try:
            terms = await lookup_terms(
                self._session,
                text=original_text,
                source_language=source_language,
                target_language=target_language,
                domain=domain,
                audience=audience,
            )
        except Exception as exc:
            logger.warning("Looking up glossary terms failed: %s", exc)
            terms = ()

        return Customization(domain=domain, audience=audience, glossary_terms=terms)


@dataclass(frozen=True)
class MessageProfile:
    """What a message's conversation is about, and what language it was in.

    A small read used off the translation path — by the correction recorder,
    which needs to stamp a correction with the conditions it was made under.
    Copied at the time rather than looked up later: a conversation profile is
    re-inferred as evidence accumulates, and this row is evidence about the
    conversation *as it was* when somebody objected to a wording.
    """

    source_language: str = ""
    domain: str = ""
    audience: str = ""


async def resolve_conversation_profile(
    session: AsyncSession, message_id: str
) -> MessageProfile:
    """Read the source language and inferred profile behind one message.

    Returns empty fields rather than raising. Every caller is doing something
    optional with the result, and a message whose conversation has never been
    profiled is the ordinary case, not an error.
    """
    if not message_id:
        return MessageProfile()

    try:
        row = (
            await session.execute(
                select(
                    Message.source_language,
                    ConversationProfile.domain,
                    ConversationProfile.audience,
                )
                .select_from(Message)
                .outerjoin(
                    ConversationProfile,
                    ConversationProfile.conversation_id == Message.conversation_id,
                )
                .where(Message.id == message_id)
            )
        ).first()
    except Exception as exc:
        logger.warning("Reading the profile behind a message failed: %s", exc)
        return MessageProfile()

    if row is None:
        return MessageProfile()
    source_language, domain, audience = row
    return MessageProfile(
        source_language=source_language or "",
        domain=domain or "",
        audience=audience or "",
    )
