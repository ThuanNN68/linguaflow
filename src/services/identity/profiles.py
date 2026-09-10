"""Reading the standing a conversation assigns to each of its members.

Two things live here because they answer one question — which bucket does this
reader belong to? — from two sides. `resolve_profiles` looks the standing up,
and `select_for_reader` decides which translation that standing entitles them to
when the exact row is missing.

Nothing here writes. Inference fills `participant_profiles`, and it is a
separate concern that runs in the background; every function below treats an
absent row as `peer` and carries on. That default is load-bearing rather than
defensive: profiles are only inferred once a conversation has enough messages,
so *every* conversation is missing them at first, and a read path that treated
that as an error would fail on the common case.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import DEFAULT_HONORIFIC_PROFILE, ParticipantProfile


class HasBucket(Protocol):
    """The two fields `select_for_reader` needs from a translation row.

    Structural rather than importing `TranslationResult`, so the ladder can be
    exercised against plain stand-ins in tests without a database.
    """

    target_language: str
    honorific_profile: str
    translation_tone: str


async def resolve_profiles(
    db: AsyncSession, conversation_id: str
) -> dict[str, str]:
    """Map every member of one conversation to their standing.

    One query, which is what the unique constraint on
    `(conversation_id, user_id)` buys: a conversation cannot hold two rows for
    one person, so there is nothing to disambiguate afterwards.

    Args:
        db: Open session.
        conversation_id: Conversation whose members are being resolved.

    Returns:
        User id mapped to standing. Members with no inferred profile are simply
        absent — callers read through `profile_for`, which supplies the default.
    """
    if not conversation_id:
        return {}

    rows = (
        await db.execute(
            select(
                ParticipantProfile.user_id,
                ParticipantProfile.honorific_profile,
            ).where(ParticipantProfile.conversation_id == conversation_id)
        )
    ).all()
    return {user_id: profile for user_id, profile in rows}


async def resolve_profiles_for_conversations(
    db: AsyncSession,
    conversation_ids: Sequence[str],
) -> dict[str, dict[str, str]]:
    """Resolve standings for several conversations at once.

    The list endpoint renders every member of every conversation the caller
    belongs to, and each of those members has a standing that is only defined
    within their own conversation. Doing this per conversation would put an N+1
    back into an endpoint that already goes out of its way to avoid one for the
    message preview.

    Args:
        db: Open session.
        conversation_ids: Conversations being listed.

    Returns:
        Conversation id mapped to that conversation's own user-to-standing
        mapping. Conversations with nothing inferred are absent, as are members
        within them; read through `profile_for` on both levels.
    """
    if not conversation_ids:
        return {}

    rows = (
        await db.execute(
            select(
                ParticipantProfile.conversation_id,
                ParticipantProfile.user_id,
                ParticipantProfile.honorific_profile,
            ).where(ParticipantProfile.conversation_id.in_(conversation_ids))
        )
    ).all()

    grouped: dict[str, dict[str, str]] = {}
    for conversation_id, user_id, profile in rows:
        grouped.setdefault(conversation_id, {})[user_id] = profile
    return grouped


def profile_for(profiles: dict[str, str], key: str) -> str:
    """Read a standing out of a mapping, defaulting to the neutral one.

    A one-line function so that the default appears once. Spelled out at each
    call site instead, one of them would eventually be written as `[key]` and
    raise KeyError on a conversation nobody has inferred yet — which is every
    conversation for its first five messages.
    """
    return profiles.get(key) or DEFAULT_HONORIFIC_PROFILE


def select_for_reader(
    rows: Iterable[HasBucket],
    *,
    target_language: str,
    honorific_profile: str,
    translation_tone: str = "natural",
) -> HasBucket | None:
    """Pick the translation a reader in this bucket should be shown.

    Three steps, in order:

    1. the exact `(language, standing)` the reader is owed;
    2. failing that, the same language at the neutral `peer` standing;
    3. failing that, any translation into that language.

    The ladder exists because standings are inferred and **can change**, while
    `translation_results.honorific_profile` is written once and never rewritten.
    Without steps 2 and 3, one re-inference would blank every translation
    already delivered in the thread: the reader reloads and the whole
    conversation reverts to untranslated text, with nothing logged to say why.
    Serving a slightly-wrong register is a far smaller harm than serving none.

    Step 3 iterates in the order it was given, so the caller decides what
    "any" means; passing rows ordered by `created_at` makes it the oldest,
    which is at least stable between two calls.

    Args:
        rows: Candidate translations, of any language.
        target_language: The language the reader reads.
        honorific_profile: The standing they hold in this conversation.

    Returns:
        The chosen row, or None when nothing has been translated into that
        language yet.
    """
    same_language = [row for row in rows if row.target_language == target_language]
    if not same_language:
        return None

    # Exact style first.  Pre-tone historical rows are natural, so changing a
    # preference must still leave those messages readable through the lower
    # natural fallback rather than making a thread appear untranslated.
    for tone in (translation_tone, "natural"):
        for row in same_language:
            if row.honorific_profile == honorific_profile and getattr(row, "translation_tone", "natural") == tone:
                return row
        for row in same_language:
            if row.honorific_profile == DEFAULT_HONORIFIC_PROFILE and getattr(row, "translation_tone", "natural") == tone:
                return row
        for row in same_language:
            if getattr(row, "translation_tone", "natural") == tone:
                return row

    return same_language[0]
