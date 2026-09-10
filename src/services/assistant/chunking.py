"""Cutting a conversation into retrievable pieces, five different ways (ADR-38).

Pure functions over plain dataclasses. Nothing here opens a session, calls an
embedding provider or reads configuration, for the reason `src/agents/guardrails.py`
gives about its own contents: the interesting behaviour is in the boundaries these
functions choose, and a boundary is only arguable when it can be asserted in a
test that needs neither a database nor a graph.

Why five. `message` — one message per chunk — is what the project has today, and
it measured hit@3 = 22% against a chance baseline of 8.8% (`sweep-20260822-064517`).
That number is the reason ADR-27 turned semantic context off for the translation
agent, and the reason the assistant cannot inherit the same approach: it has to
answer "what did we decide about the deadline" over a thread of two thousand
messages. The four other strategies each attack one specific way the one-message
chunk fails:

- a person spreads one fact over five short messages, so no single message
  contains it -> `turn_window`
- the answer sits in the middle of a two-thousand-character message, where it is
  diluted by everything else in that message -> `token_window`
- a very long thread changes subject a dozen times, and a fixed window
  straddles the boundaries -> `semantic_split`
- a small chunk retrieves precisely but carries too little context to generate
  from -> `parent_child`

Which one wins is a measurement, not an opinion; `eval/assistant_chunk_sweep.py`
is what decides it, and `strategy` is part of the unique key in
`assistant_chunks` precisely so several can coexist over identical data.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

# Messages further apart than this start a new turn window. Five minutes is not
# a tuned number and does not need to be: it separates "still typing the same
# thought" from "came back after a meeting", and the sweep varies it.
DEFAULT_TURN_GAP_SECONDS = 300
# Ceiling on a turn window regardless of timing. Without it a busy standup with
# no pause becomes one chunk covering an hour, which retrieves for everything
# and therefore discriminates nothing.
DEFAULT_TURN_MAX_MESSAGES = 8
DEFAULT_TOKEN_WINDOW = 384
DEFAULT_TOKEN_OVERLAP_RATIO = 0.25
DEFAULT_CHILD_TOKENS = 128
DEFAULT_PARENT_TOKENS = 512
# Below this cosine similarity, two consecutive messages are treated as being
# about different things. Deliberately low: a false split costs one boundary,
# while a missed split merges two subjects into a chunk that answers neither.
DEFAULT_SEMANTIC_THRESHOLD = 0.55

# Scripts whose characters carry roughly one token each, rather than the
# ~4-characters-per-token that holds for Latin text. Without this split a
# Japanese message is estimated at a quarter of its real cost and a "384 token"
# window silently becomes a 1500 token one.
_DENSE_SCRIPT = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿가-힯฀-๿]"
)


@dataclass(frozen=True, slots=True)
class SourceMessage:
    """One message, reduced to what chunking actually needs.

    Not `src.database.models.Message`: taking the ORM row would tie every test
    here to a database session, and chunking has no use for the other twenty
    columns. The caller projects rows into these.
    """

    id: str
    sender_label: str
    text: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable piece, ready to be written to `assistant_chunks`.

    ``message_ids`` is the citation trail. An answer must be able to point at
    the messages it came from; a chunk index is an artefact of retrieval and
    means nothing to a reader.
    """

    index: int
    text: str
    message_ids: tuple[str, ...] = field(default_factory=tuple)
    token_count: int = 0
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    # Set only under `parent_child`, where this chunk is a child that expands
    # into the wider chunk at that index before reaching the prompt.
    parent_index: int | None = None


def estimate_tokens(text: str) -> int:
    """Approximate the token cost of a string, without loading a tokenizer.

    An estimate on purpose. The real count depends on the tokenizer of whichever
    provider is configured, which changes with `ASSISTANT_LLM_PROVIDER`, and
    importing one would put a model download in the path of a pure function.
    What the window sizes need is a number that is *proportional* across the
    three scripts in this corpus, not an exact one.

    Latin text runs about four characters per token; Chinese, Japanese, Korean
    and Thai run about one. Counting them together at the Latin rate is what
    makes a mixed thread's Japanese half silently overflow every window.
    """
    if not text:
        return 0
    dense = len(_DENSE_SCRIPT.findall(text))
    rest = len(text) - dense
    return dense + (rest + 3) // 4


def format_line(message: SourceMessage) -> str:
    """Render one message the way it will appear inside a chunk.

    The speaker label is part of the text rather than metadata beside it: a
    retrieved chunk goes into a prompt as prose, and "who said this" is often
    the half that answers the question.
    """
    return f"{message.sender_label}: {message.text}"


def _assemble(
    index: int,
    group: Sequence[SourceMessage],
    *,
    parent_index: int | None = None,
) -> Chunk:
    """Build one chunk from whole messages."""
    text = "\n".join(format_line(message) for message in group)
    return Chunk(
        index=index,
        text=text,
        message_ids=tuple(message.id for message in group),
        token_count=estimate_tokens(text),
        starts_at=group[0].created_at,
        ends_at=group[-1].created_at,
        parent_index=parent_index,
    )


def chunk_by_message(messages: Sequence[SourceMessage]) -> list[Chunk]:
    """One chunk per message — the baseline every other strategy is measured against.

    Kept as a first-class strategy rather than as "no chunking" so that a sweep
    reports it in the same units as the rest, over the same corpus, in the same
    run. A baseline measured separately is a baseline nobody trusts.
    """
    return [_assemble(index, [message]) for index, message in enumerate(messages)]


def chunk_by_turn_window(
    messages: Sequence[SourceMessage],
    *,
    max_gap_seconds: int = DEFAULT_TURN_GAP_SECONDS,
    max_messages: int = DEFAULT_TURN_MAX_MESSAGES,
    overlap: int = 1,
) -> list[Chunk]:
    """Group consecutive messages into conversational turns.

    The failure this exists for: someone types "ok so about the deadline", then
    "we said the 15th", then "but QA needs two days", then "so the 13th really".
    No single message carries the decision, so a one-message-per-chunk index can
    retrieve at most a fragment of it however good the embedding model is.

    ``overlap`` repeats the last message of each window at the head of the next,
    so a fact that lands exactly on a boundary is still whole in one of them.

    Args:
        messages: In chronological order.
        max_gap_seconds: A longer pause starts a new window.
        max_messages: Hard ceiling, so a burst with no pauses still gets cut.
        overlap: Messages repeated from the previous window. Zero disables it.
    """
    if not messages:
        return []

    groups: list[list[SourceMessage]] = []
    current: list[SourceMessage] = [messages[0]]

    for previous, message in zip(messages, messages[1:], strict=False):
        gap = (message.created_at - previous.created_at).total_seconds()
        if gap > max_gap_seconds or len(current) >= max_messages:
            groups.append(current)
            # The overlap is taken from the window being closed, not from the
            # one being opened, so the repeated messages are the ones adjacent
            # to the boundary.
            current = list(current[-overlap:]) if overlap > 0 else []
        current.append(message)

    if current:
        groups.append(current)

    return [_assemble(index, group) for index, group in enumerate(groups)]


def _split_long_text(text: str, budget: int) -> list[str]:
    """Cut one oversized message into pieces of roughly ``budget`` tokens.

    Split on sentence-ish boundaries first and only fall back to a hard cut mid
    sentence when a single sentence exceeds the budget on its own. A hard cut
    every time would routinely sever a clause from its subject, which is exactly
    the kind of fragment that embeds to something plausible and retrieves for
    the wrong question.
    """
    if estimate_tokens(text) <= budget:
        return [text]

    pieces: list[str] = []
    buffer = ""
    # Keep the delimiter with the sentence it ends; a chunk beginning with "?"
    # is a chunk that lost its question.
    for sentence in re.split(r"(?<=[.!?。！？\n])\s*", text):
        if not sentence:
            continue
        if buffer and estimate_tokens(buffer + " " + sentence) > budget:
            pieces.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}".strip() if buffer else sentence

        while estimate_tokens(buffer) > budget:
            # One sentence longer than the whole budget. Cut it by characters,
            # using the ratio the estimate itself implies so the piece lands
            # near the budget rather than far under it.
            cut = max(1, int(len(buffer) * budget / max(estimate_tokens(buffer), 1)))
            pieces.append(buffer[:cut])
            buffer = buffer[cut:].lstrip()

    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_by_token_window(
    messages: Sequence[SourceMessage],
    *,
    target_tokens: int = DEFAULT_TOKEN_WINDOW,
    overlap_ratio: float = DEFAULT_TOKEN_OVERLAP_RATIO,
) -> list[Chunk]:
    """Pack messages into fixed token budgets, splitting messages that exceed one.

    The only strategy here that can cut *inside* a message, and the reason it
    exists: an answer buried in the middle of a two-thousand-character message
    is, to an embedding model, one signal among fifty in a single vector. Cutting
    that message into four gives the relevant quarter a vector of its own.

    A chunk produced from a split message still carries that message's id, so
    the citation trail survives the cut — several chunks pointing at one message
    is expected, and `message_ids` is an array for exactly this.

    Args:
        messages: In chronological order.
        target_tokens: Budget per chunk.
        overlap_ratio: Share of each chunk repeated at the head of the next, so
            a fact spanning a boundary is whole in at least one of them.
    """
    if not messages or target_tokens < 1:
        return []

    # (rendered line, source message) — the message travels alongside so a piece
    # of a split message still knows which message it came from.
    units: list[tuple[str, SourceMessage]] = []
    for message in messages:
        line = format_line(message)
        if estimate_tokens(line) <= target_tokens:
            units.append((line, message))
            continue
        for piece in _split_long_text(message.text, target_tokens):
            units.append((f"{message.sender_label}: {piece}", message))

    overlap_tokens = int(target_tokens * max(0.0, min(overlap_ratio, 0.9)))
    chunks: list[Chunk] = []
    start = 0

    while start < len(units):
        end = start
        used = 0
        while end < len(units):
            cost = estimate_tokens(units[end][0])
            # `end == start` guarantees progress: a unit at or over budget still
            # forms a chunk by itself rather than looping forever.
            if used and used + cost > target_tokens:
                break
            used += cost
            end += 1

        window = units[start:end]
        text = "\n".join(line for line, _ in window)
        seen: list[str] = []
        for _, message in window:
            if message.id not in seen:
                seen.append(message.id)

        chunks.append(
            Chunk(
                index=len(chunks),
                text=text,
                message_ids=tuple(seen),
                token_count=estimate_tokens(text),
                starts_at=window[0][1].created_at,
                ends_at=window[-1][1].created_at,
            )
        )

        if end >= len(units):
            break

        # Step back far enough to repeat `overlap_tokens` worth of the window
        # just closed, but never so far that the next window starts where this
        # one did — that is an infinite loop wearing a reasonable-looking hat.
        back = 0
        budget = overlap_tokens
        while back < len(window) - 1 and budget > 0:
            budget -= estimate_tokens(window[-1 - back][0])
            back += 1
        start = max(start + 1, end - back)

    return chunks


def chunk_by_semantic_split(
    messages: Sequence[SourceMessage],
    adjacent_similarity: Sequence[float],
    *,
    threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    max_messages: int = DEFAULT_TURN_MAX_MESSAGES * 2,
) -> list[Chunk]:
    """Cut where consecutive messages stop resembling each other.

    Similarities are passed in rather than computed here, which is what keeps
    this function pure and testable: embedding is a network call, and the
    boundary logic — the part that is actually arguable — deserves tests that
    run in milliseconds without a provider key.

    Args:
        messages: In chronological order.
        adjacent_similarity: Cosine similarity between message *i* and *i+1*,
            so it holds ``len(messages) - 1`` values. A shorter sequence is
            padded with a value above the threshold, which merges rather than
            splits — degrading towards `turn_window` rather than towards one
            chunk per message.
        threshold: Below this, the pair is treated as a subject change.
        max_messages: Ceiling, so a monotone thread does not become one chunk.
    """
    if not messages:
        return []

    groups: list[list[SourceMessage]] = []
    current: list[SourceMessage] = [messages[0]]

    for position, message in enumerate(messages[1:]):
        similarity = (
            adjacent_similarity[position]
            if position < len(adjacent_similarity)
            else threshold + 1.0
        )
        if similarity < threshold or len(current) >= max_messages:
            groups.append(current)
            current = []
        current.append(message)

    if current:
        groups.append(current)

    return [_assemble(index, group) for index, group in enumerate(groups)]


def chunk_parent_child(
    messages: Sequence[SourceMessage],
    *,
    child_tokens: int = DEFAULT_CHILD_TOKENS,
    parent_tokens: int = DEFAULT_PARENT_TOKENS,
) -> list[Chunk]:
    """Small chunks to retrieve with, larger chunks to generate from.

    The tension this resolves: a small chunk ranks precisely, because its single
    vector describes one thing, but hands the model too little to answer with. A
    large chunk carries enough context and ranks poorly, because its vector is
    an average of everything in it. Indexing the small one and returning the
    large one gets both.

    Returns parents and children in one list sharing an index space, because
    they share the `(conversation_id, strategy, chunk_index)` key. Parents have
    ``parent_index`` of None; children point at the parent they expand into.
    Retrieval searches children only — that is the whole point — and looks the
    parent up by index afterwards.
    """
    parents = chunk_by_token_window(
        messages, target_tokens=parent_tokens, overlap_ratio=0.0
    )
    if not parents:
        return []

    chunks: list[Chunk] = list(parents)
    by_id = {message.id: message for message in messages}

    for parent in parents:
        covered = [by_id[mid] for mid in parent.message_ids if mid in by_id]
        for child in chunk_by_token_window(
            covered, target_tokens=child_tokens, overlap_ratio=0.0
        ):
            chunks.append(
                Chunk(
                    index=len(chunks),
                    text=child.text,
                    message_ids=child.message_ids,
                    token_count=child.token_count,
                    starts_at=child.starts_at,
                    ends_at=child.ends_at,
                    parent_index=parent.index,
                )
            )

    return chunks


def build_chunks(
    strategy: str,
    messages: Sequence[SourceMessage],
    *,
    adjacent_similarity: Sequence[float] | None = None,
    **options,
) -> list[Chunk]:
    """Dispatch to one strategy by name.

    The names are the ones in `ASSISTANT_CHUNK_STRATEGIES`, which the database
    also enforces with a CheckConstraint — so an unknown name raises here rather
    than travelling to the insert and failing there, where the message would be
    about a constraint instead of about a typo.

    Raises:
        ValueError: ``strategy`` is not a known name.
    """
    if strategy == "message":
        return chunk_by_message(messages)
    if strategy == "turn_window":
        return chunk_by_turn_window(messages, **options)
    if strategy == "token_window":
        return chunk_by_token_window(messages, **options)
    if strategy == "semantic_split":
        return chunk_by_semantic_split(
            messages, adjacent_similarity or (), **options
        )
    if strategy == "parent_child":
        return chunk_parent_child(messages, **options)
    raise ValueError(f"Unknown chunking strategy: {strategy!r}")
