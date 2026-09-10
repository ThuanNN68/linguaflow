"""Turning one reader's edit into a signal the glossary can be mined from.

`translation_edits` stays what ADR-19 made it: append-only and private to its
author. Mining it directly would quietly revoke that promise, so this module
writes a separate, narrower record — what the machine wrote, what the human
wrote instead, a few words of context around it with identifiers stripped, and
the same kind of anonymised preview of the sender's own original wording — and
only when the author agreed to share it.

The extraction is by rule rather than by a model. It runs while the edit is
being saved, where an API call has no business being, and a wrong extraction
costs nothing: the miner only ever proposes a term several people corrected the
same way, so noise from one bad split never reaches the review queue.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from difflib import SequenceMatcher

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.guardrails import sanitize_context_message
from src.config import Settings, get_settings
from src.database import get_async_session_maker
from src.database.models import CorrectionLog
from src.services.shared.embeddings import embed_with_model

logger = logging.getLogger(__name__)

# Words of the surrounding text kept either side of the correction. Enough for
# an administrator to see the term in use, short enough that the snippet is not
# the conversation (docs/architecture/assistant-admin.md 3.1, option B).
SNIPPET_CONTEXT_WORDS = 5

# A correction longer than this is a rewrite of the sentence, not a quarrel
# about a term, and it is not what the glossary is for.
MAX_PHRASE_WORDS = 6

_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
# Anything that identifies a person rather than describing a thing. Removed from
# the snippet before it can reach a review queue an administrator reads.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_URL = re.compile(r"\bhttps?://\S+", re.IGNORECASE)
_DIGITS = re.compile(r"\d{4,}")

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _tokenize(text: str) -> list[str]:
    """Split into words and punctuation, keeping both."""
    return _TOKEN.findall(text or "")


def extract_correction(machine_text: str, human_text: str) -> tuple[str, str] | None:
    """Find the one span the reader replaced.

    Args:
        machine_text: What the translation said.
        human_text: What the reader wrote instead.

    Returns:
        `(machine_phrase, human_phrase)`, or None when the edit is not a term
        correction — nothing changed, several separate places changed, or the
        change is long enough to be a rewrite. Returning None is the common
        case and not a failure: most edits are somebody rephrasing a sentence,
        and only a repeated disagreement about a *term* belongs in a glossary.
    """
    machine_tokens = _tokenize(machine_text)
    human_tokens = _tokenize(human_text)
    if not machine_tokens or not human_tokens:
        return None

    replacements = [
        opcode
        for opcode in SequenceMatcher(
            a=machine_tokens, b=human_tokens, autojunk=False
        ).get_opcodes()
        if opcode[0] != "equal"
    ]
    # Exactly one changed region. Two or more means the reader reworked the
    # sentence, and no single pair of phrases describes what they meant.
    if len(replacements) != 1:
        return None

    _, i1, i2, j1, j2 = replacements[0]
    machine_phrase = " ".join(machine_tokens[i1:i2]).strip()
    human_phrase = " ".join(human_tokens[j1:j2]).strip()
    if not machine_phrase or not human_phrase:
        return None
    if i2 - i1 > MAX_PHRASE_WORDS or j2 - j1 > MAX_PHRASE_WORDS:
        return None
    return machine_phrase, human_phrase


def build_snippet(text: str, phrase: str) -> str:
    """Quote a few words around the phrase, with identifiers taken out.

    Administrators have to be able to judge a proposed term, and a term pair on
    its own does not say enough. They are also barred from reading conversation
    content (docs/architecture/assistant-admin.md, diagram 2), so what they get is this: a window
    of `SNIPPET_CONTEXT_WORDS` words either side, no names, no addresses, no
    links, no long digit runs, and nothing saying who wrote it.

    An empty `phrase` is valid and not a special case needing its own
    function: `original_snippet` calls this with one, since `machine_phrase`
    is in `target_language` and would never be found in the sender's own
    wording anyway. `start` then stays 0 and the window becomes the first
    `SNIPPET_CONTEXT_WORDS` words of `text`, anonymised the same way — a
    preview rather than a window around a match, which is the right shape for
    a text nothing here is centring on.
    """
    cleaned = _URL.sub("[link]", text or "")
    cleaned = _EMAIL.sub("[email]", cleaned)
    cleaned = _DIGITS.sub("[number]", cleaned)

    words = cleaned.split()
    needle = (phrase or "").split()
    start = 0
    for index in range(len(words)):
        if needle and words[index : index + len(needle)] == needle:
            start = index
            break

    first = max(0, start - SNIPPET_CONTEXT_WORDS)
    last = min(len(words), start + len(needle) + SNIPPET_CONTEXT_WORDS)
    window = " ".join(words[first:last])
    prefix = "… " if first > 0 else ""
    suffix = " …" if last < len(words) else ""
    return sanitize_context_message(f"{prefix}{window}{suffix}")


def schedule_correction_record(
    *,
    machine_text: str,
    human_text: str,
    original_text: str,
    source_language: str,
    target_language: str,
    domain: str,
    audience: str,
    user_id: str,
    translation_id: str,
    consent_to_share: bool,
    session_factory: Callable[[], AsyncSession] | None = None,
    settings: Settings | None = None,
) -> None:
    """Fire and forget the recording of one correction.

    Nothing is written without consent, and the check is here rather than in the
    caller so no future call site can forget it. `consent_to_share` gates the
    whole row, not just the snippet: counting a correction somebody declined to
    share would still be using it.

    Returns immediately. The reader's edit has already been saved and answered;
    this is a by-product of it.
    """
    if not consent_to_share:
        return

    extracted = extract_correction(machine_text, human_text)
    if extracted is None:
        return
    machine_phrase, human_phrase = extracted

    task = asyncio.create_task(
        _store(
            machine_phrase=machine_phrase,
            human_phrase=human_phrase,
            snippet=build_snippet(machine_text, machine_phrase),
            # No phrase to centre this one on: `machine_phrase` is in
            # `target_language` and will not appear in the sender's own
            # wording, so this is a plain anonymised preview of how the
            # message started rather than a window around a match (24/08).
            original_snippet=build_snippet(original_text, ""),
            source_language=source_language,
            target_language=target_language,
            domain=domain,
            audience=audience,
            user_id=user_id,
            translation_id=translation_id,
            session_factory=session_factory or get_async_session_maker(),
            settings=settings or get_settings(),
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _store(
    *,
    machine_phrase: str,
    human_phrase: str,
    snippet: str,
    original_snippet: str,
    source_language: str,
    target_language: str,
    domain: str,
    audience: str,
    user_id: str,
    translation_id: str,
    session_factory: Callable[[], AsyncSession],
    settings: Settings,
) -> None:
    """Write the row, embedding the corrected phrase where possible.

    The vector is what lets the miner group "staging env" with "môi trường stg"
    instead of treating them as two unrelated corrections. Without one the row
    is still recorded and still counted by exact text — a smaller signal, not a
    lost one.
    """
    try:
        vector, vector_model = await embed_with_model(human_phrase, settings=settings)
        async with session_factory() as session:
            session.add(
                CorrectionLog(
                    source_phrase=machine_phrase[:200],
                    corrected_target=human_phrase[:200],
                    source_language=source_language,
                    target_language=target_language,
                    domain=domain,
                    audience=audience,
                    user_id=user_id,
                    translation_id=translation_id,
                    consent_to_share=True,
                    anonymized_snippet=snippet,
                    original_snippet=original_snippet,
                    embedding=vector,
                    embedding_model=vector_model,
                )
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Recording a correction failed: %s", exc)
