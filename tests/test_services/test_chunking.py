"""Boundary behaviour of the five assistant chunking strategies (ADR-38).

No database and no embedding provider: every function under test is pure, which
is the reason it was written that way. What these assert is the part that is
actually arguable — where a chunk ends — plus the two loop conditions that can
hang a corpus build rather than fail it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.database.models import ASSISTANT_CHUNK_STRATEGIES
from src.services.assistant.chunking import (
    Chunk,
    SourceMessage,
    build_chunks,
    chunk_by_message,
    chunk_by_semantic_split,
    chunk_by_token_window,
    chunk_by_turn_window,
    chunk_parent_child,
    estimate_tokens,
)

BASE = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


def _messages(*texts: str, spacing_seconds: int = 30) -> list[SourceMessage]:
    return [
        SourceMessage(
            id=f"m{index}",
            sender_label=f"U0{index % 3 + 1}",
            text=text,
            created_at=BASE + timedelta(seconds=spacing_seconds * index),
        )
        for index, text in enumerate(texts)
    ]


# --- token estimation -------------------------------------------------------


def test_estimate_tokens_counts_cjk_far_higher_than_latin_of_equal_length():
    """The whole point of the estimate: mixed threads must not overflow silently.

    Counting Japanese at the Latin rate underestimates it about fourfold, which
    turns a 384-token window into a 1500-token one without any error.
    """
    latin = "abcd" * 20
    japanese = "翻訳" * 40

    assert len(latin) == len(japanese)
    assert estimate_tokens(japanese) > estimate_tokens(latin) * 3


def test_estimate_tokens_returns_zero_for_empty_text():
    assert estimate_tokens("") == 0


# --- message ---------------------------------------------------------------


def test_chunk_by_message_produces_one_chunk_per_message_with_its_id():
    chunks = chunk_by_message(_messages("first", "second", "third"))

    assert [chunk.message_ids for chunk in chunks] == [("m0",), ("m1",), ("m2",)]
    assert [chunk.index for chunk in chunks] == [0, 1, 2]


def test_chunk_by_message_keeps_the_speaker_label_inside_the_text():
    """Who said it is often the half that answers the question, so it is prose."""
    chunk = chunk_by_message(_messages("we ship on friday"))[0]

    assert chunk.text == "U01: we ship on friday"


# --- turn_window -----------------------------------------------------------


def test_turn_window_keeps_a_fact_split_across_short_messages_in_one_chunk():
    """The failure this strategy exists for: no single message holds the decision."""
    chunks = chunk_by_turn_window(
        _messages(
            "ok so about the deadline",
            "we said the 15th",
            "but QA needs two days",
            "so the 13th really",
        )
    )

    assert len(chunks) == 1
    assert "the 15th" in chunks[0].text
    assert "the 13th really" in chunks[0].text


def test_turn_window_starts_a_new_chunk_after_a_long_pause():
    messages = _messages("morning standup done", "unrelated afternoon topic")
    messages[1] = SourceMessage(
        id="m1",
        sender_label="U02",
        text="unrelated afternoon topic",
        created_at=BASE + timedelta(hours=4),
    )

    chunks = chunk_by_turn_window(messages, max_gap_seconds=300, overlap=0)

    assert len(chunks) == 2


def test_turn_window_repeats_the_boundary_message_so_a_fact_is_never_severed():
    chunks = chunk_by_turn_window(
        _messages(*[f"line {i}" for i in range(6)]),
        max_messages=3,
        overlap=1,
    )

    assert len(chunks) > 1
    # The last message of a window reappears at the head of the next one.
    assert chunks[0].message_ids[-1] == chunks[1].message_ids[0]


def test_turn_window_caps_a_burst_with_no_pauses():
    """Otherwise a busy standup becomes one chunk that retrieves for everything."""
    chunks = chunk_by_turn_window(
        _messages(*[f"line {i}" for i in range(20)], spacing_seconds=1),
        max_messages=5,
        overlap=0,
    )

    assert all(len(chunk.message_ids) <= 5 for chunk in chunks)


def test_turn_window_returns_nothing_for_an_empty_conversation():
    assert chunk_by_turn_window([]) == []


# --- token_window ----------------------------------------------------------


def test_token_window_splits_a_message_longer_than_the_whole_budget():
    """The one strategy allowed to cut inside a message, and why it exists."""
    long_text = ". ".join(f"sentence number {i} about the payment milestone" for i in range(60))
    chunks = chunk_by_token_window(_messages(long_text), target_tokens=64)

    assert len(chunks) > 1
    # Every piece still cites the message it was cut from — several chunks
    # pointing at one message is expected, which is why message_ids is an array.
    assert all(chunk.message_ids == ("m0",) for chunk in chunks)


def test_token_window_keeps_chunks_near_the_budget():
    long_text = ". ".join(f"sentence number {i} about the payment milestone" for i in range(60))
    chunks = chunk_by_token_window(_messages(long_text), target_tokens=64)

    # A little over is fine — a unit is never broken to hit the number exactly.
    assert all(chunk.token_count <= 64 * 2 for chunk in chunks)


def test_token_window_terminates_when_one_unit_exceeds_the_budget():
    """A window that cannot fit even one unit must still advance.

    Written as a regression guard rather than for coverage: the overlap step-back
    is the one loop here that can fail to make progress, and the symptom would be
    a corpus build that hangs instead of raising.
    """
    chunks = chunk_by_token_window(
        _messages("x" * 4000, "y" * 4000), target_tokens=8, overlap_ratio=0.9
    )

    assert chunks
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_token_window_rejects_a_zero_budget_by_returning_nothing():
    assert chunk_by_token_window(_messages("anything"), target_tokens=0) == []


# --- semantic_split --------------------------------------------------------


def test_semantic_split_cuts_where_consecutive_messages_stop_resembling_each_other():
    messages = _messages("deadline is the 15th", "QA needs two days", "lunch anyone")
    # m0~m1 are about the same thing; m1~m2 are not.
    chunks = chunk_by_semantic_split(messages, [0.9, 0.1], threshold=0.55)

    assert len(chunks) == 2
    assert chunks[0].message_ids == ("m0", "m1")
    assert chunks[1].message_ids == ("m2",)


def test_semantic_split_merges_rather_than_splits_when_similarities_are_missing():
    """Degrading towards turn_window is safe; degrading towards one-per-message is not.

    A missing similarity means the embedding call failed. Splitting on that would
    hand back the exact baseline this strategy exists to beat, while merging
    keeps the grouping the window would have produced anyway.
    """
    chunks = chunk_by_semantic_split(_messages("a", "b", "c"), [], threshold=0.55)

    assert len(chunks) == 1


def test_semantic_split_caps_a_thread_that_never_changes_subject():
    chunks = chunk_by_semantic_split(
        _messages(*[f"line {i}" for i in range(30)]),
        [0.99] * 29,
        threshold=0.55,
        max_messages=6,
    )

    assert all(len(chunk.message_ids) <= 6 for chunk in chunks)


# --- parent_child ----------------------------------------------------------


def test_parent_child_indexes_children_and_returns_parents_in_one_index_space():
    """Both live under one `(conversation_id, strategy, chunk_index)` key."""
    long_text = ". ".join(f"sentence {i} about the invoice" for i in range(80))
    chunks = chunk_parent_child(_messages(long_text), child_tokens=32, parent_tokens=128)

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))

    parents = [chunk for chunk in chunks if chunk.parent_index is None]
    children = [chunk for chunk in chunks if chunk.parent_index is not None]
    assert parents and children


def test_parent_child_points_every_child_at_a_parent_that_exists():
    """Retrieval looks the parent up by index, so a dangling one returns nothing."""
    long_text = ". ".join(f"sentence {i} about the invoice" for i in range(80))
    chunks = chunk_parent_child(_messages(long_text), child_tokens=32, parent_tokens=128)

    parent_indexes = {chunk.index for chunk in chunks if chunk.parent_index is None}
    for child in chunks:
        if child.parent_index is not None:
            assert child.parent_index in parent_indexes


def test_parent_child_children_are_smaller_than_their_parents():
    """If they were not, indexing the child would buy nothing over the parent."""
    long_text = ". ".join(f"sentence {i} about the invoice" for i in range(80))
    chunks = chunk_parent_child(_messages(long_text), child_tokens=32, parent_tokens=256)

    by_index = {chunk.index: chunk for chunk in chunks}
    for child in chunks:
        if child.parent_index is not None:
            assert child.token_count <= by_index[child.parent_index].token_count


# --- dispatch --------------------------------------------------------------


@pytest.mark.parametrize("strategy", ASSISTANT_CHUNK_STRATEGIES)
def test_build_chunks_handles_every_strategy_the_database_will_accept(strategy):
    """The dispatcher and the CheckConstraint must know the same set of names."""
    chunks = build_chunks(
        strategy,
        _messages("first message", "second message"),
        adjacent_similarity=[0.9],
    )

    assert chunks
    assert all(isinstance(chunk, Chunk) for chunk in chunks)


def test_build_chunks_rejects_an_unknown_strategy_before_reaching_the_database():
    with pytest.raises(ValueError, match="Unknown chunking strategy"):
        build_chunks("sliding_window", _messages("anything"))
