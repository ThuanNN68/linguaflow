"""Bounds the Assistant Agent runs inside (ADR-40).

Pure functions, so these need neither a graph nor a database — which is the
reason they were written as functions. Each covers a failure with a specific
shape: an unbounded prompt paid for on every replan round, a planner that has
stopped planning and is re-running its own calls, and a summary that quotes a
private message to the group.
"""

from __future__ import annotations

from src.agents.assistant.guardrails import (
    MAX_MEMORY_CHARS,
    MAX_REQUEST_CHARS,
    cap_memory,
    cap_request,
    drop_repeated_calls,
    leaks_private_text,
)

# --- length -----------------------------------------------------------------


def test_an_overlong_request_is_trimmed_to_the_prompt_budget():
    """This text is repeated into every planning round, so its size is paid for."""
    assert len(cap_request("x" * 40_000)) == MAX_REQUEST_CHARS


def test_a_request_is_trimmed_from_the_end_so_the_question_survives():
    """What a person asks for is in the first sentence; the tail is pasted context."""
    trimmed = cap_request("Tóm tắt giúp mình. " + "bối cảnh " * 5000)

    assert trimmed.startswith("Tóm tắt giúp mình.")


def test_an_ordinary_request_passes_through_unchanged():
    assert cap_request("  deadline là khi nào?  ") == "deadline là khi nào?"


def test_memory_is_trimmed_to_a_total_budget():
    kept = cap_memory(["x" * 1000] * 100)

    assert sum(len(line) for line in kept) <= MAX_MEMORY_CHARS


def test_memory_trimming_keeps_whole_lines():
    """Half a line has a speaker and no sentence, or the reverse.

    Both read to a model as something a person said, which is worse than the
    line being absent.
    """
    lines = [f"U01: câu số {index}" for index in range(50)]

    assert all(line in lines for line in cap_memory(lines, limit=100))


def test_memory_trimming_drops_from_the_end_not_the_front():
    """`load_memory` puts retrieved chunks first and the recent window last.

    Dropping from the front would discard what retrieval worked to find, while
    dropping from the back discards recency — and recency is the half that can
    be recovered by asking again.
    """
    lines = ["chunk retrieval found", "recent one", "recent two"]

    assert cap_memory(lines, limit=25) == ["chunk retrieval found"]


def test_memory_within_budget_is_returned_intact():
    lines = ["a", "b", "c"]

    assert cap_memory(lines) == lines


# --- repetition --------------------------------------------------------------


def test_a_call_already_made_with_the_same_arguments_is_dropped():
    """A planner asking for it again has stopped planning; the answer is in its context."""
    steps = [{"tool": "search_old_messages", "arguments": {"query": "deadline"}}]

    assert drop_repeated_calls(steps, steps) == []


def test_the_same_tool_with_different_arguments_is_not_a_repeat():
    """Two searches for two things is exactly what multi-hop questions need."""
    steps = [{"tool": "search_old_messages", "arguments": {"query": "owner"}}]
    already = [{"tool": "search_old_messages", "arguments": {"query": "deadline"}}]

    assert drop_repeated_calls(steps, already) == steps


def test_argument_order_does_not_make_two_identical_calls_look_different():
    """Models emit keys in whatever order; the call is the same call."""
    steps = [{"tool": "t", "arguments": {"a": 1, "b": 2}}]
    already = [{"tool": "t", "arguments": {"b": 2, "a": 1}}]

    assert drop_repeated_calls(steps, already) == []


def test_a_plan_repeating_itself_within_one_round_is_deduplicated():
    step = {"tool": "summarize_conversation", "arguments": {}}

    assert len(drop_repeated_calls([step, step], [])) == 1


def test_dropping_a_stale_step_keeps_the_rest_of_the_plan():
    """A plan is often stale about its first step and right about its second."""
    stale = {"tool": "search_old_messages", "arguments": {"query": "deadline"}}
    fresh = {"tool": "summarize_conversation", "arguments": {}}

    assert drop_repeated_calls([stale, fresh], [stale]) == [fresh]


# --- leakage -----------------------------------------------------------------


def test_a_summary_quoting_a_private_message_verbatim_is_recognised():
    """The last check before generated text can reach a group (ADR-31)."""
    private = (
        "Bạn còn nợ báo cáo hiệu năng từ tuần trước và quản lý đã hỏi về nó hai lần rồi"
    )

    assert leaks_private_text(f"Tóm tắt: {private} Ngoài ra nhóm chốt deadline.", [private])


def test_a_summary_quoting_only_the_opening_of_a_private_message_is_recognised():
    """A long private message is rarely copied whole; the opening is what gets quoted."""
    private = (
        "Bạn còn nợ báo cáo hiệu năng từ tuần trước và quản lý đã hỏi về nó hai lần rồi, "
        "nên mình đề nghị bạn gửi bản nháp trước thứ Sáu này."
    )

    assert leaks_private_text(f"Tóm tắt: {private[:90]}", [private])


def test_whitespace_differences_do_not_hide_a_quote():
    private = "Bạn còn nợ báo cáo hiệu năng từ tuần trước và quản lý đã hỏi hai lần"

    assert leaks_private_text("Tóm tắt:\n\n" + "  ".join(private.split()), [private])


def test_an_ordinary_summary_is_not_flagged():
    """A false positive here silently deletes a good summary, so the bar is a quote."""
    private = "Bạn còn nợ báo cáo hiệu năng từ tuần trước và quản lý đã hỏi hai lần"

    assert not leaks_private_text("Nhóm chốt deadline vào thứ Sáu.", [private])


def test_a_short_private_message_is_not_treated_as_a_quotable_run():
    """Ordinary shared vocabulary — a name, a date — must not read as a leak."""
    assert not leaks_private_text("Nhóm chốt deadline thứ Sáu", ["thứ Sáu"])


def test_nothing_private_means_nothing_to_leak():
    assert not leaks_private_text("bất kỳ nội dung nào", [])


def test_an_empty_answer_leaks_nothing():
    assert not leaks_private_text("", ["một tin nhắn riêng tư khá dài để vượt ngưỡng kiểm tra"])
