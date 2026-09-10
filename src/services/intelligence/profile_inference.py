"""Work out what a conversation is about and where its members stand.

Runs in the background, never on the request path, and never inside the
translation graph. A message is translated once per bucket, so a graph node
doing this would ask the same question up to four times for one message and get
four answers to reconcile.

The cadence is the whole design (ADR-24). Inference waits until a conversation
has something to go on, repeats only as evidence accumulates, and stops
permanently once it has stopped changing its mind:

* nothing at all below `MIN_MESSAGES_BEFORE_FIRST_RUN` messages;
* first run exactly at that mark;
* again every `MESSAGES_BETWEEN_RUNS` messages after the previous run;
* locked for good once `STABLE_RUNS_BEFORE_LOCK` consecutive runs agree.

Counting messages rather than minutes is deliberate: a conversation nobody has
written in has added no evidence, and re-deciding on a timer would spend quota
to reach the same answer. Locking matters for a different reason — a profile
that keeps moving makes the register visibly change mid-thread, which reads to
the participants as the product being erratic.

Known limitation, recorded in ADR-24: there is no way for a person to correct a
standing, so the lock will hold a wrong conclusion as firmly as a right one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.guardrails import sanitize_context_message
from src.agents.prompts import INFER_CONVERSATION_PROFILE_PROMPT
from src.database import get_async_session_maker
from src.database.models import (
    GLOSSARY_AUDIENCES,
    GLOSSARY_DOMAINS,
    HONORIFIC_PROFILES,
    ConversationProfile,
    Message,
    ParticipantProfile,
)
from src.services.language.glossary import normalize_scope
from src.services.messaging.message_visibility import public_only
from src.services.shared.llm import extract_text, get_llm

logger = logging.getLogger(__name__)

MIN_MESSAGES_BEFORE_FIRST_RUN = 5
MESSAGES_BETWEEN_RUNS = 20
STABLE_RUNS_BEFORE_LOCK = 3

# How much of the conversation the model is shown. Wider than the three to five
# lines a translation gets, because standing shows up over a stretch of
# exchanges rather than in any one message, and narrow enough to stay cheap.
TRANSCRIPT_MESSAGES = 30

# Strong references to running tasks, for the same reason `schedule_translations`
# keeps them: asyncio holds only a weak reference and will garbage-collect a
# task nobody is awaiting, mid-statement.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def schedule_profile_inference(
    *,
    conversation_id: str,
    session_factory: Callable[[], AsyncSession] | None = None,
    llm_factory: Callable[[], Any] | None = None,
) -> None:
    """Fire and forget an inference run for one conversation.

    Returns immediately. The caller is handling a message that has already been
    delivered, and nothing about this may hold that up or fail it.

    Args:
        conversation_id: Conversation to consider.
        session_factory: Session source; defaults to the application's.
        llm_factory: Model source; defaults to the configured provider. Injected
            so tests never reach the network.
    """
    if not conversation_id:
        return

    task = asyncio.create_task(
        _run(
            conversation_id=conversation_id,
            session_factory=session_factory or get_async_session_maker(),
            llm_factory=llm_factory or get_llm,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _run(
    *,
    conversation_id: str,
    session_factory: Callable[[], AsyncSession],
    llm_factory: Callable[[], Any],
) -> None:
    """Decide whether a run is due, and carry it out if so.

    Swallows everything. This is a background improvement to translation
    quality; a conversation whose profile fails to update keeps the profile it
    had, or none, and translation carries on either way.
    """
    try:
        async with session_factory() as session:
            profile, message_count = await _load_state(session, conversation_id)
            if not _is_due(profile, message_count):
                return

            transcript, alias_to_user = await _build_transcript(session, conversation_id)
            if not transcript:
                return

        raw = await _ask_model(llm_factory, transcript)
        inferred = _parse_inference(raw, alias_to_user)
        if inferred is None:
            return

        async with session_factory() as session:
            await _apply(session, conversation_id, message_count, inferred)
    except Exception as exc:
        logger.warning(
            "Inferring the profile for conversation %s failed: %s", conversation_id, exc
        )


async def _load_state(
    session: AsyncSession, conversation_id: str
) -> tuple[ConversationProfile | None, int]:
    """Read the stored profile and how many messages the conversation holds."""
    profile = await session.scalar(
        select(ConversationProfile).where(
            ConversationProfile.conversation_id == conversation_id
        )
    )
    message_count = (
        await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.deleted_at.is_(None),
            )
        )
    ) or 0
    return profile, int(message_count)


def _is_due(profile: ConversationProfile | None, message_count: int) -> bool:
    """Decide whether this conversation has earned another run.

    Args:
        profile: The stored profile, or None when nothing has been inferred.
        message_count: Live messages in the conversation.

    Returns:
        True when a run should happen now.
    """
    if message_count < MIN_MESSAGES_BEFORE_FIRST_RUN:
        return False
    if profile is None:
        return True
    if profile.locked_at is not None:
        return False
    return message_count - profile.message_count_at_last_run >= MESSAGES_BETWEEN_RUNS


async def _build_transcript(
    session: AsyncSession, conversation_id: str
) -> tuple[str, dict[str, str]]:
    """Render the recent conversation, and say which alias is whom.

    Speakers are aliased `U01`, `U02` in order of first appearance, exactly as
    `DatabaseContextProvider` does for translation context. The model is not
    told anyone's name — it has no use for one, and sending it would widen what
    leaves the conversation for no gain (ADR-15).

    The mapping back to user ids never leaves this module; it exists only so the
    answer can be written to the right rows.

    Returns:
        The rendered transcript and the alias-to-user-id mapping. An empty
        transcript means there was nothing worth sending.
    """
    rows = (
        await session.execute(
            select(Message.sender_id, Message.original_text)
            .where(
                Message.conversation_id == conversation_id,
                Message.deleted_at.is_(None),
                Message.original_text != "",
                # The profile it infers is applied to the whole conversation, so
                # a message only one member can see must not shape how everyone
                # else is addressed.
                public_only(),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(TRANSCRIPT_MESSAGES)
        )
    ).all()

    alias_by_user: dict[str, str] = {}
    lines: list[str] = []
    for sender_id, text in reversed(rows):
        alias = alias_by_user.get(sender_id)
        if alias is None:
            alias = f"U{len(alias_by_user) + 1:02d}"
            alias_by_user[sender_id] = alias
        cleaned = sanitize_context_message(text)
        if cleaned:
            lines.append(f"- {alias}: {cleaned}")

    if not lines:
        return "", {}
    return "\n".join(lines), {alias: user for user, alias in alias_by_user.items()}


async def _ask_model(llm_factory: Callable[[], Any], transcript: str) -> str:
    """Send the transcript and return whatever came back, as text."""
    llm = llm_factory()
    response = await llm.ainvoke(
        [
            {
                "role": "user",
                "content": INFER_CONVERSATION_PROFILE_PROMPT.format(
                    transcript=transcript,
                    domains=", ".join(f"`{value}`" for value in GLOSSARY_DOMAINS),
                    audiences=", ".join(f"`{value}`" for value in GLOSSARY_AUDIENCES),
                ),
            }
        ]
    )
    return extract_text(response)


def _parse_inference(
    raw: str, alias_to_user: Mapping[str, str]
) -> dict[str, Any] | None:
    """Turn the model's answer into rows, or into None.

    None on anything unexpected rather than a partial result. A half-understood
    answer would be written to the database and then counted towards the
    stability that locks the profile forever, so the bar for accepting one is
    that it parses completely.

    Args:
        raw: The model's reply.
        alias_to_user: Mapping produced alongside the transcript.

    Returns:
        A dict with `domain`, `audience`, `rationale` and `standings`
        (user id to standing), or None.
    """
    text = (raw or "").strip()
    if not text:
        return None

    # Providers add a code fence despite being asked not to often enough that
    # tolerating it is cheaper than losing the whole run to it.
    if text.startswith("```"):
        text = text.strip("`")
        _, _, text = text.partition("\n")
        text = text.strip()

    try:
        payload = json.loads(text)
    except ValueError:
        logger.warning("Profile inference returned %d characters of non-JSON", len(text))
        return None
    if not isinstance(payload, dict):
        return None

    participants = payload.get("participants")
    if not isinstance(participants, dict):
        return None

    standings: dict[str, str] = {}
    for alias, standing in participants.items():
        user_id = alias_to_user.get(str(alias))
        if user_id is None:
            # An alias nobody spoke under. Ignoring it is right: the model
            # invented a speaker, and there is no row to write.
            continue
        if standing not in HONORIFIC_PROFILES:
            return None
        standings[user_id] = standing

    if not standings:
        return None

    return {
        "domain": normalize_scope(payload.get("domain"), GLOSSARY_DOMAINS),
        "audience": normalize_scope(payload.get("audience"), GLOSSARY_AUDIENCES),
        "rationale": str(payload.get("rationale") or "")[:1000],
        "standings": standings,
    }


async def _apply(
    session: AsyncSession,
    conversation_id: str,
    message_count: int,
    inferred: Mapping[str, Any],
) -> None:
    """Store the result and advance the stability counter.

    The counter is what eventually stops the runs. A run counts as unchanged
    when the subject area, the audience, and every standing it *just inferred*
    match what was already stored.

    "Just inferred" is the load-bearing part. Demanding that the whole stored
    set be reproduced looks stricter and is actually broken: the transcript is a
    window on recent messages, so a member who stops posting drops out of it,
    their standing is absent from the answer, the comparison fails, and the
    counter resets. One quiet participant would keep a conversation unlocked
    forever and spend a model call every twenty messages for the rest of its
    life. Saying nothing about somebody is not the same as changing one's mind
    about them.

    A participant appearing for the first time does still reset it: that is new
    information, and `existing.get` returns None for them.

    Rows for members not in this answer are left exactly as they are.
    """
    profile = await session.scalar(
        select(ConversationProfile).where(
            ConversationProfile.conversation_id == conversation_id
        )
    )
    existing = await _stored_standings(session, conversation_id)
    unchanged = (
        profile is not None
        and profile.domain == inferred["domain"]
        and profile.audience == inferred["audience"]
        and all(
            existing.get(user_id) == standing
            for user_id, standing in inferred["standings"].items()
        )
    )

    if profile is None:
        profile = ConversationProfile(conversation_id=conversation_id)
        session.add(profile)

    profile.domain = inferred["domain"]
    profile.audience = inferred["audience"]
    profile.rationale = inferred["rationale"]
    profile.message_count_at_last_run = message_count
    profile.consecutive_stable_runs = (
        profile.consecutive_stable_runs + 1 if unchanged else 1
    )
    if profile.consecutive_stable_runs >= STABLE_RUNS_BEFORE_LOCK:
        profile.locked_at = func.now()

    for user_id, standing in inferred["standings"].items():
        row = await session.scalar(
            select(ParticipantProfile).where(
                ParticipantProfile.conversation_id == conversation_id,
                ParticipantProfile.user_id == user_id,
            )
        )
        if row is None:
            row = ParticipantProfile(
                conversation_id=conversation_id, user_id=user_id
            )
            session.add(row)
        row.honorific_profile = standing
        row.inferred_by = "llm"
        row.rationale = inferred["rationale"]

    await session.commit()


async def _stored_standings(
    session: AsyncSession, conversation_id: str
) -> dict[str, str]:
    """The standings already on record, for the stability comparison."""
    rows: Sequence[Any] = (
        await session.execute(
            select(
                ParticipantProfile.user_id,
                ParticipantProfile.honorific_profile,
            ).where(ParticipantProfile.conversation_id == conversation_id)
        )
    ).all()
    return {user_id: standing for user_id, standing in rows}
