"""Tests for the Assistant Agent's planner-executor graph and its human gate.

The load-bearing assertion is that `human_confirm` genuinely suspends: a graph
that ran straight through it would approve everything while every other test
still passed, which is precisely the failure the mandatory-confirmation rule
exists to prevent (`docs/architecture/assistant-admin.md` §1.3).

Nothing here reaches a network. The planner's model is injected, and the two
executors are patched at their service boundary — what is under test is the
graph's routing and gating, not the quality of a summary.

Fixtures live in this file rather than tests/conftest.py, which is shared across
all feature areas.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agents.assistant import build_assistant_graph
from src.agents.assistant.graph import (
    _parse_plan,
    make_respond,
    route_after_plan,
    route_after_tools,
)
from src.agents.assistant.state import MAX_REPLANS
from src.database.models import AGENT_CONSENT_SCOPES


def planner_returning(*tools: str, clarification: str | None = None, done: bool = False) -> Any:
    """Build a model whose only job is to answer the planner prompt."""
    steps = ", ".join(
        f'{{"tool": "{name}", "arguments": {{}}, "reason": "test"}}' for name in tools
    )
    question = "null" if clarification is None else json.dumps(clarification)
    payload = (
        f'{{"steps": [{steps}], "clarification": {question}, '
        f'"done": {"true" if done else "false"}}}'
    )
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=MagicMock(content=payload))
    return model


def planner_sequence(*payloads: Any) -> Any:
    """A factory whose model answers differently on each planning round.

    Needed because the interesting behaviour of a replan loop is what happens
    between rounds, and a model returning the same plan every time cannot
    exercise it.

    Returns the *factory*, not the model, and builds the model exactly once.
    `plan` calls its factory on every round, so a factory that constructed a
    fresh mock each time would replay the sequence from the start and the loop
    would look like it never advanced — which is how the first version of this
    helper reported three searches where the plan asked for one.
    """
    model = MagicMock()
    model.ainvoke = AsyncMock(
        side_effect=[MagicMock(content=payload) for payload in payloads]
    )
    return lambda: model


def config(thread: str) -> dict[str, Any]:
    """A thread id is what the checkpointer keys a suspended run on."""
    return {"configurable": {"thread_id": thread}}


@pytest.fixture(autouse=True)
def permitted_and_quiet(monkeypatch):
    """Grant the permission and silence the memory read, by default.

    `check_consent` and `load_memory` both query for real, which is the point of
    them — neither can be satisfied by seeding the state, and a test that could
    seed its way past a permission check would not be testing the check. So they
    are patched at their boundary here, and the two tests that are *about* those
    nodes override this.
    """
    monkeypatch.setattr(
        "src.agents.assistant.graph.require_consent", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.services.messaging.chat.ChatService.get_message_history", AsyncMock(return_value=[])
    )
    # Every tool permission granted, so the planner is offered the full
    # catalogue — but `store_memory` withheld, because semantic recall is
    # additive and tests about routing should not depend on an embedding
    # provider being reachable.
    #
    # Both entry points are patched: `available_tools` asks once through
    # `get_consents`, while `load_memory` still asks about the one scope it
    # cares about.
    async def granted_except_memory(db, user_id, scope):
        return scope != "store_memory"

    async def consents(db, user_id):
        return [
            SimpleNamespace(scope=scope, is_granted=scope != "store_memory")
            for scope in AGENT_CONSENT_SCOPES
        ]

    monkeypatch.setattr("src.services.assistant.agent_consent.has_consent", granted_except_memory)
    monkeypatch.setattr("src.services.assistant.agent_consent.get_consents", consents)


@pytest.mark.asyncio
async def test_the_graph_stops_at_the_human_gate_instead_of_confirming(monkeypatch) -> None:
    """The whole point of the gate: nothing is executed before a person answers."""
    confirm = AsyncMock()
    monkeypatch.setattr(
        "src.services.intelligence.action_proposals.ActionProposalService.confirm_proposal", confirm
    )

    async def fake_extract(self, **kwargs):
        return [SimpleNamespace(model_dump=lambda mode: {"id": "p-1", "title": "Gửi báo cáo"})]

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "extract_actions_from_message",
        fake_extract,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("extract_actions"),
    )
    result = await graph.ainvoke(
        {
            "conversation_id": "c-1",
            "user_id": "u-1",
            "request_text": "@assistant nhắc mình việc này",
            "memory": ["Tôi sẽ gửi báo cáo sáng mai"],
            "telemetry": {"source_message_id": "m-1"},
        },
        config("thread-gate"),
    )

    assert "__interrupt__" in result
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_resuming_the_gate_confirms_only_the_proposals_the_person_named(
    monkeypatch,
) -> None:
    """Silence is not consent: an unnamed proposal stays pending, not rejected."""
    confirmed: list[str] = []

    async def fake_confirm(
        self, proposal_id, user_id, corrections=None, reminder_minutes_before=15
    ):
        confirmed.append(proposal_id)
        return SimpleNamespace(id=proposal_id, title=f"Việc {proposal_id}")

    monkeypatch.setattr(
        "src.services.intelligence.action_proposals.ActionProposalService.confirm_proposal", fake_confirm
    )

    async def fake_extract(self, **kwargs):
        return [
            SimpleNamespace(model_dump=lambda mode: {"id": "p-1", "title": "A"}),
            SimpleNamespace(model_dump=lambda mode: {"id": "p-2", "title": "B"}),
        ]

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "extract_actions_from_message",
        fake_extract,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("extract_actions"),
    )
    thread = config("thread-resume")
    await graph.ainvoke(
        {
            "conversation_id": "c-1",
            "user_id": "u-1",
            "request_text": "nhắc mình",
            "telemetry": {"source_message_id": "m-1"},
        },
        thread,
    )

    final = await graph.ainvoke(
        Command(resume={"approved_proposal_ids": ["p-1"]}),
        thread,
    )

    assert confirmed == ["p-1"]
    assert "Việc p-1" in final["reply"]


@pytest.mark.asyncio
async def test_a_missing_permission_ends_the_run_before_any_message_is_read(
    monkeypatch,
) -> None:
    """`check_consent` is the first node for a reason."""
    from src.services.assistant.agent_consent import ConsentRequiredError

    monkeypatch.setattr(
        "src.agents.assistant.graph.require_consent",
        AsyncMock(side_effect=ConsentRequiredError("read_conversations")),
    )
    loaded = AsyncMock(return_value=[])
    monkeypatch.setattr("src.services.messaging.chat.ChatService.get_message_history", loaded)

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("summarize_conversation"),
    )
    result = await graph.ainvoke(
        {
            "conversation_id": "c-1",
            "user_id": "u-1",
            "request_text": "tóm tắt giúp mình",
        },
        config("thread-consent"),
    )

    loaded.assert_not_awaited()
    assert "read_conversations" in result["reply"]


@pytest.mark.asyncio
async def test_a_summary_request_never_reaches_the_human_gate(monkeypatch) -> None:
    """Approving a summary would be a confirmation dialog with nothing to decide."""

    async def fake_summarize(self, **kwargs):
        return SimpleNamespace(
            summary="Nhóm chốt deadline thứ sáu",
            message_count=3,
            model_dump=lambda mode: {"summary": "Nhóm chốt deadline thứ sáu", "key_points": []},
        )

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "summarize_conversation",
        fake_summarize,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("summarize_conversation"),
    )
    result = await graph.ainvoke(
        {
            "conversation_id": "c-1",
            "user_id": "u-1",
            "request_text": "tóm tắt giúp mình",
        },
        config("thread-summary"),
    )

    assert "__interrupt__" not in result
    assert result["reply"] == "Nhóm chốt deadline thứ sáu"


@pytest.mark.asyncio
async def test_a_planner_outage_still_answers_by_falling_back_to_summarizing(
    monkeypatch,
) -> None:
    """No node raises: an LLM outage must not become a broken chat feature."""

    async def fake_summarize(self, **kwargs):
        return SimpleNamespace(
            summary="Bản tóm tắt dự phòng",
            message_count=1,
            model_dump=lambda mode: {"summary": "Bản tóm tắt dự phòng", "key_points": []},
        )

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "summarize_conversation",
        fake_summarize,
    )

    broken = MagicMock()
    broken.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))

    graph = build_assistant_graph(
        db=None, checkpointer=InMemorySaver(), llm_factory=lambda: broken
    )
    result = await graph.ainvoke(
        {
            "conversation_id": "c-1",
            "user_id": "u-1",
            "request_text": "giúp mình với",
        },
        config("thread-outage"),
    )

    assert result["reply"] == "Bản tóm tắt dự phòng"


def test_the_planner_cannot_invent_a_tool_that_reaches_nothing() -> None:
    """An unknown name would dispatch nowhere and finish looking successful."""
    steps, _, _ = _parse_plan(
        '{"steps": [{"tool": "delete_everything", "arguments": {}, "reason": "x"}]}',
        known_tools={"summarize_conversation"},
    )
    assert steps == []


@pytest.mark.asyncio
async def test_a_plan_naming_no_known_tool_still_produces_a_reply() -> None:
    """Routing to `respond` rather than off the end of the graph."""
    assert route_after_plan({"plan": []}) == "respond"
    assert (await make_respond()({}))["reply"]


def test_a_planner_that_asks_a_question_routes_to_clarify() -> None:
    """Asking is a successful outcome; guessing which meeting to move is not."""
    assert route_after_plan({"clarification": "Cuộc họp nào?"}) == "clarify"


def test_proposals_reach_the_gate_before_the_planner_gets_another_turn() -> None:
    """Otherwise a replan could stack a second proposal on an unseen first.

    The person would then be approving a list they never saw assembled, which
    is the one thing the mandatory gate exists to prevent.
    """
    assert route_after_tools({"proposals": [{"id": "p1"}], "replan_count": 0}) == "human_confirm"


def test_the_replan_loop_stops_at_its_budget() -> None:
    """A planner that can always ask for one more tool will.

    Bounded in code rather than by the model: a planner asked to count its own
    turns miscounts, and each round is a model call the person waits through.
    """
    assert route_after_tools({"replan_count": 0}) == "plan"
    assert route_after_tools({"replan_count": MAX_REPLANS}) == "respond"


def test_the_planner_survives_a_model_that_wraps_json_in_a_code_fence() -> None:
    """Providers add fences unprompted; a strict parser would read that as failure."""
    steps, _, _ = _parse_plan(
        '```json\n{"steps": [{"tool": "summarize_conversation", "arguments": {},'
        ' "reason": "r"}]}\n```',
        known_tools={"summarize_conversation"},
    )
    assert [step["tool"] for step in steps] == ["summarize_conversation"]


def test_a_planner_declaring_itself_done_asks_for_no_more_tools() -> None:
    """Repeating a call already made returns the same answer and spends a round."""
    steps, _, done = _parse_plan(
        '{"steps": [{"tool": "summarize_conversation", "arguments": {}, "reason": "r"}],'
        ' "done": true}',
        known_tools={"summarize_conversation"},
    )
    assert done is True
    # The parser still reports what was asked for; `plan` is what drops it, so
    # the two decisions stay separable.
    assert len(steps) == 1


def test_unparseable_planner_output_asks_for_nothing_rather_than_guessing() -> None:
    steps, clarification, done = _parse_plan("not json at all", known_tools={"x"})

    assert (steps, clarification, done) == ([], None, False)


@pytest.mark.asyncio
async def test_semantic_recall_reaches_past_the_recent_window(monkeypatch) -> None:
    """Recall answers a question the recent window cannot.

    The two passes exist for different reasons: the time-ordered window is
    always right about recency, and retrieval reaches things said weeks ago that
    no limit would have included. Retrieval now runs over `assistant_chunks`
    rather than over `message_embeddings` (ADR-37), so what comes back is a
    chunk of transcript rather than a single message.

    Retrieved chunks lead. They cannot be interleaved into one timeline the way
    single messages could: a chunk may span three messages weeks apart, and
    sorting it by its earliest line would place it in a position that implies a
    continuity it does not have.
    """
    from datetime import UTC, datetime

    old = SimpleNamespace(
        id="m-old",
        original_text="Chốt deadline là 30/9",
        deleted_at=None,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    recent = SimpleNamespace(
        id="m-recent",
        original_text="Sáng nay họp lúc 9h",
        deleted_at=None,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )

    monkeypatch.setattr(
        "src.services.messaging.chat.ChatService.get_message_history", AsyncMock(return_value=[recent])
    )
    monkeypatch.setattr(
        "src.services.assistant.agent_consent.has_consent", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "src.services.assistant.assistant_retrieval.retrieve",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    chunk_id="k-old",
                    chunk_index=0,
                    text=old.original_text,
                    message_ids=(old.id,),
                )
            ]
        ),
    )

    captured: dict[str, Any] = {}

    async def fake_summarize(self, **kwargs):
        return SimpleNamespace(
            summary="ok",
            message_count=2,
            model_dump=lambda mode: {"summary": "ok", "key_points": []},
        )

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "summarize_conversation",
        fake_summarize,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("summarize_conversation"),
    )
    state = await graph.ainvoke(
        {
            "conversation_id": "c-1",
            "user_id": "u-1",
            "request_text": "deadline là khi nào",
        },
        config("thread-recall"),
    )
    captured["memory"] = state["memory"]

    # The retrieved chunk leads, then the recent window in order.
    assert captured["memory"] == ["Chốt deadline là 30/9", "Sáng nay họp lúc 9h"]


@pytest.mark.asyncio
async def test_a_recalled_message_already_in_the_window_is_not_repeated(
    monkeypatch,
) -> None:
    """A duplicated line reads to the model as the thing having been said twice.

    Skipped only when *every* message the chunk covers is already in the window.
    A chunk that also reaches further back still earns its place, because the
    part outside the window is the part the window could not supply.
    """
    from datetime import UTC, datetime

    same = SimpleNamespace(
        id="m-1",
        original_text="Chốt deadline là 30/9",
        deleted_at=None,
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )

    monkeypatch.setattr(
        "src.services.messaging.chat.ChatService.get_message_history", AsyncMock(return_value=[same])
    )
    monkeypatch.setattr(
        "src.services.assistant.agent_consent.has_consent", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "src.services.assistant.assistant_retrieval.retrieve",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    chunk_id="k-1",
                    chunk_index=0,
                    text=same.original_text,
                    message_ids=(same.id,),
                )
            ]
        ),
    )

    async def fake_summarize(self, **kwargs):
        return SimpleNamespace(
            summary="ok",
            message_count=1,
            model_dump=lambda mode: {"summary": "ok", "key_points": []},
        )

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "summarize_conversation",
        fake_summarize,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("summarize_conversation"),
    )
    state = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "deadline"},
        config("thread-dedupe"),
    )

    assert state["memory"] == ["Chốt deadline là 30/9"]


@pytest.mark.asyncio
async def test_a_chunk_reaching_past_the_window_is_kept_even_if_it_overlaps_it(
    monkeypatch,
) -> None:
    """Overlap is not duplication when part of the chunk is outside the window.

    Dropping any chunk that touches the window would discard exactly the chunks
    that do the work: a `turn_window` chunk covering a decision and the two
    replies to it usually straddles the boundary, and the half outside is the
    half the window could not supply.
    """
    from datetime import UTC, datetime

    recent = SimpleNamespace(
        id="m-recent",
        original_text="Sáng nay họp lúc 9h",
        deleted_at=None,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )

    monkeypatch.setattr(
        "src.services.messaging.chat.ChatService.get_message_history",
        AsyncMock(return_value=[recent]),
    )
    monkeypatch.setattr(
        "src.services.assistant.agent_consent.has_consent", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "src.services.assistant.assistant_retrieval.retrieve",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    chunk_id="k-straddle",
                    chunk_index=0,
                    text="Chốt deadline là 30/9\nSáng nay họp lúc 9h",
                    message_ids=("m-old", "m-recent"),
                )
            ]
        ),
    )

    async def fake_summarize(self, **kwargs):
        return SimpleNamespace(
            summary="ok",
            message_count=2,
            model_dump=lambda mode: {"summary": "ok", "key_points": []},
        )

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "summarize_conversation",
        fake_summarize,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("summarize_conversation"),
    )
    state = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "deadline"},
        config("thread-straddle"),
    )

    assert "Chốt deadline là 30/9" in state["memory"][0]


@pytest.mark.asyncio
async def test_recall_is_skipped_without_the_store_memory_permission(monkeypatch) -> None:
    """Retrieval is always on; the permission is what gates it, not a flag.

    `RAG_CONTEXT_ENABLED` governs the translation agent and stays off (ADR-27).
    For the assistant, retrieval is the feature — so the only thing that can
    switch it off for a given person is that person not having agreed to it.
    """
    from datetime import UTC, datetime

    recent = SimpleNamespace(
        id="m-recent",
        original_text="Sáng nay họp lúc 9h",
        deleted_at=None,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )
    retrieval = AsyncMock(return_value=[])

    monkeypatch.setattr(
        "src.services.messaging.chat.ChatService.get_message_history",
        AsyncMock(return_value=[recent]),
    )
    monkeypatch.setattr(
        "src.services.assistant.agent_consent.has_consent", AsyncMock(return_value=False)
    )
    monkeypatch.setattr("src.services.assistant.assistant_retrieval.retrieve", retrieval)

    async def fake_summarize(self, **kwargs):
        return SimpleNamespace(
            summary="ok",
            message_count=1,
            model_dump=lambda mode: {"summary": "ok", "key_points": []},
        )

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "summarize_conversation",
        fake_summarize,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("summarize_conversation"),
    )
    state = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "deadline"},
        config("thread-no-consent"),
    )

    retrieval.assert_not_awaited()
    assert state["memory"] == ["Sáng nay họp lúc 9h"]


@pytest.mark.asyncio
async def test_the_planner_can_act_on_what_the_first_round_of_tools_returned(
    monkeypatch,
) -> None:
    """The point of replanning: a second decision informed by the first result.

    A planner that cannot see what its tools returned is not planning, only
    retrying — and the multi-step requests this agent exists for ("what did we
    decide, and put it on my calendar") need the answer to the first half before
    the second half can be expressed at all.
    """
    seen: list[str] = []

    async def fake_search(db, *, conversation_id, query_text, config=None, settings=None):
        seen.append(query_text)
        return [
            SimpleNamespace(
                chunk_id="k1", chunk_index=0, text="Chot deadline 13/9", message_ids=("m1",)
            )
        ]

    monkeypatch.setattr("src.services.assistant.assistant_retrieval.retrieve", fake_search)

    async def fake_summarize(self, **kwargs):
        return SimpleNamespace(
            summary="Tom tat sau khi tim",
            message_count=1,
            model_dump=lambda mode: {"summary": "Tom tat sau khi tim", "key_points": []},
        )

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "summarize_conversation",
        fake_summarize,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=planner_sequence(
            json.dumps(
                {
                    "steps": [
                        {
                            "tool": "search_old_messages",
                            "arguments": {"query": "deadline"},
                            "reason": "find it",
                        }
                    ],
                    "clarification": None,
                    "done": False,
                }
            ),
            json.dumps(
                {
                    "steps": [
                        {
                            "tool": "summarize_conversation",
                            "arguments": {},
                            "reason": "now summarise",
                        }
                    ],
                    "clarification": None,
                    "done": False,
                }
            ),
            json.dumps({"steps": [], "clarification": None, "done": True}),
        ),
    )
    result = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "deadline roi tom tat"},
        config("thread-replan"),
    )

    assert seen == ["deadline"]
    assert result["reply"] == "Tom tat sau khi tim"


@pytest.mark.asyncio
async def test_a_run_stops_after_its_replan_budget_rather_than_looping(
    monkeypatch,
) -> None:
    """A planner that can always ask for one more tool will.

    Each round is a model call the person waits through, so the ceiling is in
    code. Without it a planner that keeps asking for the same search never
    reaches `respond`.
    """
    calls = {"n": 0}

    async def fake_search(db, *, conversation_id, query_text, config=None, settings=None):
        calls["n"] += 1
        return []

    monkeypatch.setattr("src.services.assistant.assistant_retrieval.retrieve", fake_search)

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        # Always asks for one more search, and never says it is done.
        llm_factory=lambda: planner_returning("search_old_messages"),
    )
    result = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "tim mai"},
        config("thread-budget"),
    )

    assert calls["n"] <= MAX_REPLANS
    assert result["reply"]


@pytest.mark.asyncio
async def test_an_ambiguous_request_is_asked_about_rather_than_guessed(
    monkeypatch,
) -> None:
    """Choosing one of two meetings is the worst outcome available here.

    The person would not learn a choice was made on their behalf until they
    missed something — which is why the planner is told to ask, and why asking
    routes to its own terminal node instead of falling through to `respond`.
    """
    question = "Ban muon doi cuoc hop sang hay chieu thu Ba?"
    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning(clarification=question),
    )
    result = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "doi cuoc hop giup minh"},
        config("thread-clarify"),
    )

    assert result["reply"] == question


@pytest.mark.asyncio
async def test_approving_carries_the_persons_corrections_into_the_confirmation(
    monkeypatch,
) -> None:
    """What a direct calendar write could never offer.

    By the time somebody saw a direct write it would have happened; here they
    can fix the time the extractor read wrong and say how far ahead they want to
    be nudged, and both reach `confirm_proposal` unchanged.
    """
    captured: dict[str, Any] = {}

    async def fake_confirm(
        self, proposal_id, user_id, corrections=None, reminder_minutes_before=15
    ):
        captured["corrections"] = corrections
        captured["reminder"] = reminder_minutes_before
        return SimpleNamespace(id=proposal_id, title="Review thiet ke")

    monkeypatch.setattr(
        "src.services.intelligence.action_proposals.ActionProposalService.confirm_proposal", fake_confirm
    )

    async def fake_extract(self, **kwargs):
        return [SimpleNamespace(model_dump=lambda mode: {"id": "p-1", "title": "Review"})]

    monkeypatch.setattr(
        "src.services.intelligence.conversation_intelligence.ConversationIntelligenceService."
        "extract_actions_from_message",
        fake_extract,
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("extract_actions"),
    )
    thread = config("thread-annotate")
    await graph.ainvoke(
        {
            "conversation_id": "c-1",
            "user_id": "u-1",
            "request_text": "nhac minh",
            "telemetry": {"source_message_id": "m-1"},
        },
        thread,
    )
    await graph.ainvoke(
        Command(
            resume={
                "approved_proposal_ids": ["p-1"],
                "annotations": {
                    "p-1": {"title": "Review thiet ke", "reminder_minutes_before": 120}
                },
            }
        ),
        thread,
    )

    assert captured["reminder"] == 120
    assert captured["corrections"] == {"title": "Review thiet ke"}


@pytest.mark.asyncio
async def test_a_failing_tool_does_not_end_the_run(monkeypatch) -> None:
    """The graph's rule is that no node raises, and a tool is the likeliest to fail."""

    async def broken_search(db, **kwargs):
        raise RuntimeError("retrieval provider down")

    monkeypatch.setattr("src.services.assistant.assistant_retrieval.retrieve", broken_search)

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=planner_sequence(
            json.dumps(
                {
                    "steps": [
                        {
                            "tool": "search_old_messages",
                            "arguments": {"query": "x"},
                            "reason": "try",
                        }
                    ],
                    "clarification": None,
                    "done": False,
                }
            ),
            json.dumps({"steps": [], "clarification": None, "done": True}),
        ),
    )
    result = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "tim giup"},
        config("thread-toolfail"),
    )

    assert result["reply"]


@pytest.mark.asyncio
async def test_a_person_with_no_permissions_at_all_is_told_rather_than_answered_emptily(
    monkeypatch,
) -> None:
    """Planning against an empty catalogue would answer with nothing and no reason."""
    monkeypatch.setattr(
        "src.services.assistant.agent_consent.get_consents",
        AsyncMock(
            return_value=[
                SimpleNamespace(scope=scope, is_granted=False)
                for scope in AGENT_CONSENT_SCOPES
            ]
        ),
    )

    graph = build_assistant_graph(
        db=None,
        checkpointer=InMemorySaver(),
        llm_factory=lambda: planner_returning("summarize_conversation"),
    )
    result = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "tom tat"},
        config("thread-notools"),
    )

    assert "read_conversations" in result["reply"]


@pytest.mark.asyncio
async def test_a_question_is_answered_from_what_the_tools_found(monkeypatch) -> None:
    """Without this stage a successful search still answers with the generic reply.

    The person would be told nothing was found when the search had returned
    exactly what they asked for.
    """

    async def fake_search(db, *, conversation_id, query_text, config=None, settings=None):
        return [
            SimpleNamespace(
                chunk_id="k1",
                chunk_index=0,
                text="U01: Chot deadline milestone la ngay 13/9",
                message_ids=("m1",),
            )
        ]

    monkeypatch.setattr("src.services.assistant.assistant_retrieval.retrieve", fake_search)

    planner = planner_sequence(
        json.dumps(
            {
                "steps": [
                    {
                        "tool": "search_old_messages",
                        "arguments": {"query": "deadline"},
                        "reason": "find it",
                    }
                ],
                "clarification": None,
                "done": False,
            }
        ),
        json.dumps({"steps": [], "clarification": None, "done": True}),
    )
    answerer = MagicMock()
    answerer.ainvoke = AsyncMock(
        return_value=MagicMock(content="Deadline milestone la ngay 13/9.")
    )

    calls = {"n": 0}

    def factory():
        # The planner runs twice, then the answering stage runs once. One
        # factory serves both because `make_respond` and `make_plan` are handed
        # the same one by `build_assistant_graph`.
        calls["n"] += 1
        return answerer if calls["n"] > 2 else planner()

    graph = build_assistant_graph(
        db=None, checkpointer=InMemorySaver(), llm_factory=factory
    )
    result = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "deadline la khi nao"},
        config("thread-answer"),
    )

    assert result["reply"] == "Deadline milestone la ngay 13/9."


@pytest.mark.asyncio
async def test_an_answer_quoting_a_private_message_is_discarded(monkeypatch) -> None:
    """The answering stage gets the same leak check the summary path gets.

    This text is built from the recent window as well as from retrieval, and the
    window returns what the *caller* may read — which includes the assistant's
    own private replies to them (ADR-31).
    """
    private = (
        "Ban con no bao cao hieu nang tu tuan truoc va quan ly da hoi ve no hai lan roi"
    )

    async def fake_search(db, *, conversation_id, query_text, config=None, settings=None):
        return [
            SimpleNamespace(
                chunk_id="k1", chunk_index=0, text="U01: khong lien quan", message_ids=("m1",)
            )
        ]

    monkeypatch.setattr("src.services.assistant.assistant_retrieval.retrieve", fake_search)

    # The private text has to come from `load_memory`, which is where the run
    # actually meets it — seeding `private_texts` into the initial state would
    # be overwritten by that node and the test would pass for the wrong reason.
    from datetime import UTC, datetime

    monkeypatch.setattr(
        "src.services.messaging.chat.ChatService.get_message_history",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="m-private",
                    original_text=private,
                    visibility="private",
                    deleted_at=None,
                    created_at=datetime(2026, 8, 27, tzinfo=UTC),
                )
            ]
        ),
    )

    leaking = MagicMock()
    leaking.ainvoke = AsyncMock(return_value=MagicMock(content=f"Tom tat: {private}"))
    planner = planner_sequence(
        json.dumps(
            {
                "steps": [
                    {
                        "tool": "search_old_messages",
                        "arguments": {"query": "x"},
                        "reason": "r",
                    }
                ],
                "clarification": None,
                "done": False,
            }
        ),
        json.dumps({"steps": [], "clarification": None, "done": True}),
    )
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return leaking if calls["n"] > 2 else planner()

    graph = build_assistant_graph(
        db=None, checkpointer=InMemorySaver(), llm_factory=factory
    )
    result = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "toi con no gi"},
        config("thread-answer-leak"),
    )

    assert private not in result["reply"]


@pytest.mark.asyncio
async def test_a_failure_while_answering_still_produces_a_reply(monkeypatch) -> None:
    """A worse answer rather than no answer — the graph's rule, at its last node."""

    async def fake_search(db, *, conversation_id, query_text, config=None, settings=None):
        return [
            SimpleNamespace(
                chunk_id="k1", chunk_index=0, text="U01: something", message_ids=("m1",)
            )
        ]

    monkeypatch.setattr("src.services.assistant.assistant_retrieval.retrieve", fake_search)

    broken = MagicMock()
    broken.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))
    planner = planner_sequence(
        json.dumps(
            {
                "steps": [
                    {
                        "tool": "search_old_messages",
                        "arguments": {"query": "x"},
                        "reason": "r",
                    }
                ],
                "clarification": None,
                "done": False,
            }
        ),
        json.dumps({"steps": [], "clarification": None, "done": True}),
    )
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return broken if calls["n"] > 2 else planner()

    graph = build_assistant_graph(
        db=None, checkpointer=InMemorySaver(), llm_factory=factory
    )
    result = await graph.ainvoke(
        {"conversation_id": "c-1", "user_id": "u-1", "request_text": "hoi gi do"},
        config("thread-answer-fail"),
    )

    assert result["reply"]
