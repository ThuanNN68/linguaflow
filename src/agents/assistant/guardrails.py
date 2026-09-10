"""Bounds the Assistant Agent runs inside, as plain functions.

Separate from `src/agents/guardrails.py`, which belongs to the translation agent
and guards a different shape of work: an input length cap sized for one chat
message, a `target_language` check, an output-language verification. None of that
applies to an agent that reads a thousand-message thread and plans several steps.

Functions rather than node logic, for the reason ADR-12 and ADR-13 give about
their translation counterparts: what is arguable here is where each bound sits,
and a bound deserves a test that needs neither a graph nor a database.

Three bounds, each for a failure that has a specific shape:

- **Length.** A request or a transcript can be arbitrarily long, and the cost of
  a prompt is paid per round of a loop that runs up to `MAX_REPLANS` times.
- **Repetition.** A planner that asks for the same tool with the same arguments
  twice has stopped planning. Left alone it burns the whole budget re-running a
  call whose answer it already has.
- **Leakage.** A summary is generated from a transcript the reader is entitled
  to, but the assistant also writes private replies, and a generated summary
  that quotes one verbatim would put it in front of the group (ADR-31).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

logger = logging.getLogger(__name__)

# What one request may be. Generous — a person pasting a paragraph of context is
# doing something reasonable — and finite, because this text is repeated into
# every planning round and is also the most obvious place to try to smuggle in
# instructions.
MAX_REQUEST_CHARS = 4000

# What the recalled conversation may be, in total. Sized against the planner
# prompt rather than against the model's context window: the window would hold
# far more, and paying for it every round buys nothing, because the planner is
# choosing tools rather than reading for content.
MAX_MEMORY_CHARS = 24000

# The shortest run of characters worth checking for a verbatim leak. Short
# enough to catch a copied sentence, long enough that ordinary shared vocabulary
# — a project name, a date — does not read as one.
MIN_LEAK_RUN = 60


def cap_request(text: str, *, limit: int = MAX_REQUEST_CHARS) -> str:
    """Trim a request to something a prompt can carry every round.

    Trimmed from the end rather than the middle. What a person asks for is
    almost always in the first sentence; the tail of an overlong request is
    pasted context, and losing it costs less than losing the question.
    """
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    logger.info("Assistant request trimmed from %d to %d characters", len(cleaned), limit)
    return cleaned[:limit]


def cap_memory(lines: Sequence[str], *, limit: int = MAX_MEMORY_CHARS) -> list[str]:
    """Trim recalled conversation to a total budget, keeping the newest.

    From the end backwards, because `load_memory` puts retrieved chunks first and
    the recent window last: dropping from the front would discard the chunks
    retrieval worked to find, while dropping from the back discards the recency
    the window supplies — and the window is the half that can be reconstructed
    by asking again.

    Whole lines only. Half a line has a speaker and no sentence, or a sentence
    and no speaker, and both read to a model as something a person said.
    """
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) > limit:
            break
        kept.append(line)
        used += len(line)
    if len(kept) < len(lines):
        logger.info(
            "Assistant memory trimmed from %d to %d lines", len(lines), len(kept)
        )
    return kept


def _signature(step: dict) -> str:
    """A stable identity for one planned call: the tool and its arguments.

    Arguments are serialised with sorted keys, so two calls that differ only in
    the order a model happened to emit them are recognised as the same call.
    """
    return json.dumps(
        {"tool": step.get("tool"), "arguments": step.get("arguments") or {}},
        sort_keys=True,
        ensure_ascii=False,
    )


def drop_repeated_calls(
    steps: Sequence[dict], already_called: Sequence[dict]
) -> list[dict]:
    """Remove planned calls that have already been made with the same arguments.

    A planner that asks for the same thing twice has stopped planning: the answer
    is in its own context, and re-running the call returns it again at the cost
    of a round. Dropping the step rather than refusing the plan keeps the rest of
    it usable, which matters because a plan is often right about its second step
    and stale about its first.

    Args:
        steps: What the planner just asked for.
        already_called: Every call made so far in this run, same shape.
    """
    seen = {_signature(step) for step in already_called}
    kept: list[dict] = []
    for step in steps:
        signature = _signature(step)
        if signature in seen:
            logger.info("Dropped a repeated assistant tool call: %s", step.get("tool"))
            continue
        seen.add(signature)
        kept.append(step)
    return kept


def leaks_private_text(answer: str, private_texts: Sequence[str]) -> bool:
    """Whether generated text reproduces a private message closely enough to matter.

    The assistant's own replies inside a group are private to one person
    (ADR-31), and `assistant_chunks` keeps them out of retrieval entirely — so
    this is a second line rather than the first. It exists because the recent
    window is *not* filtered that way: `get_message_history` returns what the
    caller may read, which includes their own private replies, and a summary
    generated from that window and then shown to the group would carry them.

    Matched on a run of characters rather than on similarity. A paraphrase of a
    private message is a judgement call and needs a model to make it; a verbatim
    run is a fact, and it is the case that actually happens — models quote.
    """
    if not answer:
        return False
    haystack = " ".join(answer.split()).casefold()
    for private in private_texts:
        needle = " ".join((private or "").split()).casefold()
        if len(needle) < MIN_LEAK_RUN:
            continue
        if needle in haystack:
            return True
        # A long private message is rarely quoted whole; the opening is what
        # gets copied, so the first run of it is checked too.
        if needle[:MIN_LEAK_RUN] in haystack:
            return True
    return False
