"""Indexing and hybrid retrieval over `assistant_chunks` (ADR-37).

These touch the real database, because the parts worth asserting are the parts
Postgres decides: the `embedding_model` filter, the conversation scope, the
lexical operators, and the unique key that lets several chunkings coexist.

The embedding provider is stubbed throughout. A deterministic toy embedder makes
the vector arm's ranking predictable, which is what turns "did retrieval work"
into an assertion rather than an impression — and it keeps the suite off the
Gemini free tier, which is twenty requests a day.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.database.models import AssistantChunk, Message
from src.services.assistant import assistant_indexing, assistant_retrieval
from src.services.assistant.assistant_retrieval import RetrievalConfig, retrieve

pytestmark = pytest.mark.asyncio

BASE = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)

# A vocabulary the toy embedder projects onto. Each word owns one dimension, so
# a text's vector is its word histogram and cosine similarity is genuine lexical
# overlap — crude, deterministic, and enough to rank a relevant chunk above an
# irrelevant one without any network call.
_AXES = [
    "deadline",
    "invoice",
    "database",
    "design",
    "qa",
    "release",
    "lunch",
    "standup",
]


def _toy_vector(text: str) -> list[float]:
    lowered = text.lower()
    vector = [float(lowered.count(word)) for word in _AXES]
    # A constant tail keeps the vector non-zero for text containing no axis word,
    # so cosine distance is defined for every row rather than only most of them.
    return vector + [0.1] * (768 - len(_AXES))


@pytest.fixture
def toy_embeddings(monkeypatch):
    """Replace the embedding provider on both the write and the read path."""

    async def fake_embed(text, *, settings=None):
        return _toy_vector(text or "")

    monkeypatch.setattr(assistant_indexing, "embed", fake_embed)
    monkeypatch.setattr(assistant_retrieval, "embed", fake_embed)
    monkeypatch.setattr(
        assistant_indexing, "embedding_model_name", lambda settings=None: "toy-v1"
    )
    monkeypatch.setattr(
        assistant_retrieval, "embedding_model_name", lambda settings=None: "toy-v1"
    )
    return fake_embed


@pytest.fixture
def no_reranker(monkeypatch):
    """Keep the cross-encoder out of the suite.

    Loading it would pull torch into the test process, which `embeddings.py`
    already refuses to do for the same reason. Reranking is exercised by the
    evaluation harness, where a model download is expected.
    """
    monkeypatch.setattr(
        assistant_retrieval, "_load_cross_encoder", lambda model_name: None
    )


@pytest_asyncio.fixture
async def seeded_conversation(test_db, test_user, test_user_two, conversation_factory):
    """A conversation whose subjects are separable by the toy embedder."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], "group", "retrieval"
    )

    lines = [
        (test_user, "the deadline for the milestone is the 15th"),
        (test_user_two, "qa needs two days so the deadline moves to the 13th"),
        (test_user, "lunch at the usual place"),
        (test_user_two, "the invoice for the design work went out yesterday"),
        (test_user, "database migration is scheduled after the release"),
    ]
    for index, (sender, body) in enumerate(lines):
        test_db.add(
            Message(
                conversation_id=conversation.id,
                client_message_id=f"seed-{index}",
                sender_id=sender.id,
                original_text=body,
                source_language="en",
                # A minute apart, so turn windows and orderings are decided by
                # the timestamps rather than by a tiebreaker on a random uuid.
                created_at=BASE + timedelta(minutes=index),
            )
        )
    await test_db.commit()
    return conversation


# --- indexing ---------------------------------------------------------------


async def test_index_conversation_writes_one_row_per_chunk(
    test_db, seeded_conversation, toy_embeddings
):
    written = await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    rows = (
        await test_db.scalars(
            select(AssistantChunk).where(
                AssistantChunk.conversation_id == seeded_conversation.id
            )
        )
    ).all()
    assert written == len(rows) == 5


async def test_index_conversation_records_the_messages_each_chunk_covers(
    test_db, seeded_conversation, toy_embeddings
):
    """The citation trail: an answer points at messages, not at chunk indexes."""
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="turn_window"
    )

    rows = (
        await test_db.scalars(
            select(AssistantChunk).where(
                AssistantChunk.conversation_id == seeded_conversation.id
            )
        )
    ).all()
    covered = {mid for row in rows for mid in json.loads(row.message_ids)}
    assert len(covered) == 5


async def test_index_conversation_keeps_two_strategies_side_by_side(
    test_db, seeded_conversation, toy_embeddings
):
    """`strategy` is in the unique key so a sweep compares over identical data."""
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="turn_window"
    )

    strategies = set(
        (
            await test_db.scalars(
                select(AssistantChunk.strategy).where(
                    AssistantChunk.conversation_id == seeded_conversation.id
                )
            )
        ).all()
    )
    assert strategies == {"message", "turn_window"}


async def test_index_conversation_replaces_rather_than_duplicates_on_reindex(
    test_db, seeded_conversation, toy_embeddings
):
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    rows = (
        await test_db.scalars(
            select(AssistantChunk).where(
                AssistantChunk.conversation_id == seeded_conversation.id
            )
        )
    ).all()
    assert len(rows) == 5


async def test_index_conversation_excludes_a_private_assistant_reply(
    test_db, seeded_conversation, test_user, toy_embeddings
):
    """A chunk is shared across readers, so private text must never enter one.

    A chunk can gather six messages, so it cannot be filtered per reader after
    the fact — half a chunk is not something retrieval can return. Keeping
    private messages out of the index is the only filter that composes.
    """
    test_db.add(
        Message(
            conversation_id=seeded_conversation.id,
            client_message_id="seed-private",
            sender_id=test_user.id,
            original_text="private reminder about the confidential deadline",
            source_language="en",
            visibility="private",
            visible_to_user_id=test_user.id,
            created_at=BASE + timedelta(minutes=10),
        )
    )
    await test_db.commit()

    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    texts = " ".join(
        (
            await test_db.scalars(
                select(AssistantChunk.chunk_text).where(
                    AssistantChunk.conversation_id == seeded_conversation.id
                )
            )
        ).all()
    )
    assert "confidential" not in texts


async def test_index_conversation_returns_zero_for_a_conversation_with_no_messages(
    test_db, test_user, conversation_factory, toy_embeddings
):
    empty = await conversation_factory(test_user, [test_user], "group", "empty")

    assert (
        await assistant_indexing.index_conversation(
            test_db, conversation_id=empty.id, strategy="message"
        )
        == 0
    )


async def test_index_conversation_skips_embedding_parents_under_parent_child(
    test_db, seeded_conversation, toy_embeddings
):
    """Parents are returned, never searched, so a vector for one is wasted quota."""
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="parent_child"
    )

    rows = (
        await test_db.scalars(
            select(AssistantChunk).where(
                AssistantChunk.conversation_id == seeded_conversation.id
            )
        )
    ).all()
    parents = [row for row in rows if row.parent_index is None]
    children = [row for row in rows if row.parent_index is not None]

    assert parents and children
    assert all(row.embedding is None for row in parents)
    assert all(row.embedding is not None for row in children)


# --- retrieval --------------------------------------------------------------


async def test_retrieve_ranks_the_chunk_that_answers_the_question_first(
    test_db, seeded_conversation, toy_embeddings, no_reranker
):
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    results = await retrieve(
        test_db,
        conversation_id=seeded_conversation.id,
        query_text="what did we decide about the deadline",
        config=RetrievalConfig(strategy="message", top_k=5, top_n=2, use_rerank=False),
    )

    assert results
    assert "deadline" in results[0].text


async def test_retrieve_never_reaches_into_another_conversation(
    test_db, seeded_conversation, test_user, conversation_factory, toy_embeddings, no_reranker
):
    """The leak ADR-21 catches on the way out, introduced on the way in."""
    other = await conversation_factory(test_user, [test_user], "group", "other")
    test_db.add(
        Message(
            conversation_id=other.id,
            client_message_id="seed-other",
            sender_id=test_user.id,
            original_text="the deadline in the other thread is the 30th",
            source_language="en",
            created_at=BASE,
        )
    )
    await test_db.commit()

    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )
    await assistant_indexing.index_conversation(
        test_db, conversation_id=other.id, strategy="message"
    )

    results = await retrieve(
        test_db,
        conversation_id=seeded_conversation.id,
        query_text="deadline",
        config=RetrievalConfig(strategy="message", top_k=10, top_n=10, use_rerank=False),
    )

    assert results
    assert all("other thread" not in result.text for result in results)


async def test_retrieve_ignores_chunks_written_by_a_different_embedding_model(
    test_db, seeded_conversation, toy_embeddings, no_reranker, monkeypatch
):
    """Comparing across two embedding spaces returns a confident, meaningless rank.

    This is the filter `semantic_search.py` is missing and `glossary.py` applies;
    the assistant's table must not repeat it. Failure here is silent by nature —
    the rows come back ranked, and the ranking means nothing.
    """
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    monkeypatch.setattr(
        assistant_retrieval, "embedding_model_name", lambda settings=None: "other-v2"
    )
    results = await retrieve(
        test_db,
        conversation_id=seeded_conversation.id,
        query_text="deadline",
        config=RetrievalConfig(strategy="message", top_k=5, top_n=5, use_rerank=False),
    )

    assert results == []


async def test_retrieve_finds_an_exact_term_the_vector_arm_ranks_poorly(
    test_db, seeded_conversation, toy_embeddings, no_reranker
):
    """What the lexical arm is for: a term outside the embedding's vocabulary.

    "yesterday" is on no axis of the toy embedder, so the vector arm cannot
    distinguish the chunk containing it. Full-text matching can, and fusion is
    what lets one arm's blind spot be covered by the other.
    """
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    results = await retrieve(
        test_db,
        conversation_id=seeded_conversation.id,
        query_text="yesterday",
        config=RetrievalConfig(
            strategy="message", top_k=5, top_n=3, use_rerank=False, use_lexical=True
        ),
    )

    assert any("yesterday" in result.text for result in results)


async def test_retrieve_returns_the_parent_when_a_child_matched(
    test_db, seeded_conversation, toy_embeddings, no_reranker
):
    """Retrieve with the small chunk, generate from the large one."""
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="parent_child"
    )

    results = await retrieve(
        test_db,
        conversation_id=seeded_conversation.id,
        query_text="deadline",
        config=RetrievalConfig(
            strategy="parent_child",
            top_k=5,
            top_n=2,
            use_rerank=False,
            use_parent_expansion=True,
        ),
    )

    assert results
    parent_indexes = set(
        (
            await test_db.scalars(
                select(AssistantChunk.chunk_index).where(
                    AssistantChunk.conversation_id == seeded_conversation.id,
                    AssistantChunk.parent_index.is_(None),
                )
            )
        ).all()
    )
    assert all(result.chunk_index in parent_indexes for result in results)


async def test_retrieve_returns_nothing_when_the_conversation_is_not_indexed(
    test_db, seeded_conversation, toy_embeddings, no_reranker
):
    assert (
        await retrieve(
            test_db,
            conversation_id=seeded_conversation.id,
            query_text="deadline",
            config=RetrievalConfig(strategy="message", use_rerank=False),
        )
        == []
    )


async def test_retrieve_returns_nothing_for_a_blank_query_without_calling_a_provider(
    test_db, seeded_conversation
):
    assert (
        await retrieve(test_db, conversation_id=seeded_conversation.id, query_text="   ")
        == []
    )


async def test_retrieve_degrades_to_empty_when_the_embedding_provider_is_down(
    test_db, seeded_conversation, toy_embeddings, no_reranker, monkeypatch
):
    """A worse answer, not a failed request — the caller still has its window."""
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    async def broken_embed(text, *, settings=None):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(assistant_retrieval, "embed", broken_embed)

    assert (
        await retrieve(
            test_db,
            conversation_id=seeded_conversation.id,
            query_text="deadline",
            config=RetrievalConfig(
                strategy="message", use_rerank=False, use_lexical=False
            ),
        )
        == []
    )


# --- incremental indexing ---------------------------------------------------


async def test_incremental_indexing_appends_only_the_new_messages(
    test_db, seeded_conversation, test_user, toy_embeddings
):
    """What makes keeping the index current affordable on the message path.

    A full rebuild of a two-thousand message thread every twenty messages would
    spend a hundred times the embedding budget to change a few chunks at the
    end.
    """
    first = await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    test_db.add(
        Message(
            conversation_id=seeded_conversation.id,
            client_message_id="seed-later",
            sender_id=test_user.id,
            original_text="one more thing about the release",
            source_language="en",
            created_at=BASE + timedelta(minutes=30),
        )
    )
    await test_db.commit()

    added = await assistant_indexing.index_conversation(
        test_db,
        conversation_id=seeded_conversation.id,
        strategy="message",
        incremental=True,
    )

    assert first == 5
    assert added == 1


async def test_incremental_indexing_keeps_the_chunks_that_were_already_there(
    test_db, seeded_conversation, test_user, toy_embeddings
):
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )
    test_db.add(
        Message(
            conversation_id=seeded_conversation.id,
            client_message_id="seed-later",
            sender_id=test_user.id,
            original_text="one more thing about the release",
            source_language="en",
            created_at=BASE + timedelta(minutes=30),
        )
    )
    await test_db.commit()
    await assistant_indexing.index_conversation(
        test_db,
        conversation_id=seeded_conversation.id,
        strategy="message",
        incremental=True,
    )

    rows = (
        await test_db.scalars(
            select(AssistantChunk).where(
                AssistantChunk.conversation_id == seeded_conversation.id
            )
        )
    ).all()

    assert len(rows) == 6
    # Indexes continue rather than restarting, so the unique key holds and
    # `parent_index` stays addressable.
    assert sorted(row.chunk_index for row in rows) == [0, 1, 2, 3, 4, 5]


async def test_incremental_indexing_writes_nothing_when_no_message_is_new(
    test_db, seeded_conversation, toy_embeddings
):
    """The common case on the message path, so it must cost nothing."""
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    added = await assistant_indexing.index_conversation(
        test_db,
        conversation_id=seeded_conversation.id,
        strategy="message",
        incremental=True,
    )

    assert added == 0


async def test_a_rebuild_replaces_what_an_incremental_pass_appended(
    test_db, seeded_conversation, test_user, toy_embeddings
):
    """The periodic rebuild is what removes the seams incremental passes leave."""
    await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )
    test_db.add(
        Message(
            conversation_id=seeded_conversation.id,
            client_message_id="seed-later",
            sender_id=test_user.id,
            original_text="one more thing about the release",
            source_language="en",
            created_at=BASE + timedelta(minutes=30),
        )
    )
    await test_db.commit()
    await assistant_indexing.index_conversation(
        test_db,
        conversation_id=seeded_conversation.id,
        strategy="message",
        incremental=True,
    )

    rebuilt = await assistant_indexing.index_conversation(
        test_db, conversation_id=seeded_conversation.id, strategy="message"
    )

    rows = (
        await test_db.scalars(
            select(AssistantChunk).where(
                AssistantChunk.conversation_id == seeded_conversation.id
            )
        )
    ).all()
    assert rebuilt == len(rows) == 6


async def test_scheduling_chunk_indexing_does_nothing_during_a_test_run():
    """Guarded the way `embed_with_model` guards its local fallback.

    Indexing embeds every chunk it builds, so leaving the background scheduler
    armed would put real provider calls behind any test that grants
    `store_memory` and sends a message — spending quota on assertions that are
    not about indexing, and making the suite's runtime depend on a rate limit.
    """
    assistant_indexing.schedule_chunk_index(
        conversation_id="c-1", sender_id="u-1"
    )

    assert not assistant_indexing._BACKGROUND_TASKS


async def test_scheduling_chunk_indexing_ignores_a_message_with_no_sender():
    """No configuration flag substitutes for a person having agreed (ADR-30)."""
    assistant_indexing.schedule_chunk_index(conversation_id="c-1", sender_id=None)

    assert not assistant_indexing._BACKGROUND_TASKS
