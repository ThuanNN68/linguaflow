"""The Assistant Agent's tool registry (ADR-40).

These assert the properties that make a closed registry safer than
function-calling, and each one is a thing that would fail silently: a write tool
mislabelled as a read skips the human gate; a tool offered without its permission
produces an apology instead of an answer; an argument schema that accepts
anything lets a hallucinated parameter through to a service.

No database. The tools are bound to `None` and never actually run except where a
test replaces the callable, because what is under test here is the registration
and the dispatch, not the services underneath — those have their own tests.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agents.tools.registry import (
    ToolArgumentError,
    ToolResult,
    available_tools,
    build_registry,
    call_tool,
    parse_arguments,
    render_catalogue,
)
from src.database.models import AGENT_CONSENT_SCOPES


def _registry():
    return build_registry(None, conversation_id="c-1", user_id="u-1")


# --- registration -----------------------------------------------------------


def test_every_tool_that_can_change_a_calendar_produces_a_proposal_instead():
    """`produces_proposals` is what routes a run through the human gate.

    Asserted as an exact set rather than per tool: adding a tool and forgetting
    the flag is the mistake this catches, and a per-tool assertion would pass
    for every tool that already exists while the new one slips through.
    """
    gated = {name for name, spec in _registry().items() if spec.produces_proposals}

    assert gated == {"propose_calendar_event", "extract_actions"}


def test_no_tool_writes_to_a_calendar_directly():
    """Everything goes through `action_proposals` and the one gate (ADR-34).

    A direct write also has nothing to annotate: by the time the person sees it,
    it has happened — so they cannot correct the time or say how far ahead they
    want to be nudged.
    """
    assert "create_calendar_event" not in _registry()
    assert "create_reminder" not in _registry()


def test_reading_the_calendar_does_not_require_permission_to_write_to_it():
    """Two scopes, because being shown a calendar is not being able to change it."""
    registry = _registry()

    assert registry["list_calendar_events"].consent_scope == "calendar_read"
    assert registry["propose_calendar_event"].consent_scope == "calendar_write"


def test_every_tool_declares_a_scope_the_consent_vocabulary_contains():
    """A scope outside the vocabulary raises inside `has_consent`, mid-run."""
    for spec in _registry().values():
        assert spec.consent_scope in AGENT_CONSENT_SCOPES


def test_no_tool_takes_the_user_or_conversation_as_an_argument():
    """The planner decides what to do; it never decides whose.

    Both are closed over from the authenticated request. Exposing either as a
    field would make "act on somebody else's calendar" a thing a plan could
    express, and the only defence would then be a check somewhere downstream.
    """
    for spec in _registry().values():
        fields = set(spec.schema.model_fields)
        assert not fields & {"user_id", "conversation_id", "owner_user_id"}


# --- argument validation ----------------------------------------------------


def test_arguments_reject_a_parameter_the_planner_invented():
    registry = _registry()

    with pytest.raises(ToolArgumentError, match="Extra inputs"):
        parse_arguments(registry["search_old_messages"], {"query": "x", "made_up": 1})


def test_arguments_reject_a_relative_time_where_an_instant_is_required():
    """"tomorrow morning" reaching the calendar service becomes a wrong entry."""
    registry = _registry()

    with pytest.raises(ToolArgumentError, match="starts_at"):
        parse_arguments(
            registry["propose_calendar_event"],
            {"title": "review", "starts_at": "tomorrow morning"},
        )


def test_arguments_reject_a_memory_kind_outside_the_database_vocabulary():
    """Otherwise this surfaces as a CheckConstraint violation inside a transaction."""
    registry = _registry()

    with pytest.raises(ToolArgumentError, match="kind"):
        parse_arguments(registry["save_user_memory"], {"kind": "nonsense", "content": "x"})


def test_arguments_name_the_offending_field_so_a_replan_can_fix_it():
    """The message is fed back to the planner as an observation."""
    registry = _registry()

    with pytest.raises(ToolArgumentError) as caught:
        parse_arguments(registry["search_old_messages"], {})

    assert "query" in str(caught.value)


def test_arguments_fill_in_the_defaults_the_planner_left_out():
    registry = _registry()

    parsed = parse_arguments(registry["search_old_messages"], {"query": "deadline"})

    assert parsed["top_n"] == 4


# --- consent filtering ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_whose_permission_is_missing_is_not_offered_to_the_planner(
    monkeypatch,
):
    """Filtered before planning, not refused after.

    A planner shown a tool it may not use will propose it, the run refuses, and
    the person gets an apology rather than an answer. A planner shown only what
    it may do plans around the restriction by itself.
    """

    async def only_reading(db, user_id):
        return [
            SimpleNamespace(scope=scope, is_granted=scope == "read_conversations")
            for scope in AGENT_CONSENT_SCOPES
        ]

    monkeypatch.setattr("src.services.assistant.agent_consent.get_consents", only_reading)

    offered = await available_tools(None, user_id="u-1", registry=_registry())

    assert set(offered) == {
        "search_old_messages",
        "summarize_conversation",
        "extract_actions",
    }


@pytest.mark.asyncio
async def test_granting_every_permission_offers_every_tool(monkeypatch):
    monkeypatch.setattr(
        "src.services.assistant.agent_consent.get_consents",
        AsyncMock(
            return_value=[
                SimpleNamespace(scope=scope, is_granted=True)
                for scope in AGENT_CONSENT_SCOPES
            ]
        ),
    )

    offered = await available_tools(None, user_id="u-1", registry=_registry())

    assert set(offered) == set(_registry())


@pytest.mark.asyncio
async def test_granting_nothing_offers_nothing(monkeypatch):
    monkeypatch.setattr(
        "src.services.assistant.agent_consent.get_consents",
        AsyncMock(
            return_value=[
                SimpleNamespace(scope=scope, is_granted=False)
                for scope in AGENT_CONSENT_SCOPES
            ]
        ),
    )

    assert await available_tools(None, user_id="u-1", registry=_registry()) == {}


# --- dispatch ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_tool_becomes_an_observation_rather_than_an_exception():
    """The graph's rule is that no node raises, and a tool is the likeliest to fail.

    With the failure in hand the planner can try something else or say what did
    not work. An exception ends the run and the person gets neither.
    """

    async def explodes(**kwargs):
        raise RuntimeError("calendar provider unavailable")

    spec = replace(_registry()["list_calendar_events"], run=explodes)

    result = await call_tool(spec, {})

    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert "calendar provider unavailable" in result.summary


@pytest.mark.asyncio
async def test_bad_arguments_become_an_observation_without_running_the_tool():
    ran = {"called": False}

    async def should_not_run(**kwargs):
        ran["called"] = True
        return ToolResult(tool="x", ok=True, summary="")

    spec = replace(_registry()["search_old_messages"], run=should_not_run)

    result = await call_tool(spec, {"query": "x", "made_up": 1})

    assert result.ok is False
    assert ran["called"] is False


@pytest.mark.asyncio
async def test_a_successful_tool_returns_what_it_produced():
    async def works(*, query, top_n):
        return ToolResult(tool="search_old_messages", ok=True, summary=f"found {query}")

    spec = replace(_registry()["search_old_messages"], run=works)

    result = await call_tool(spec, {"query": "deadline"})

    assert result.ok is True
    assert result.summary == "found deadline"


# --- prompt rendering -------------------------------------------------------


def test_the_catalogue_marks_which_arguments_are_optional():
    """A planner that has to guess the shape guesses wrong and wastes a round trip."""
    rendered = render_catalogue(_registry())

    assert '"search_old_messages"(query, top_n?)' in rendered


def test_the_catalogue_lists_every_registered_tool():
    rendered = render_catalogue(_registry())

    for name in _registry():
        assert f'"{name}"' in rendered


def test_the_catalogue_never_reveals_which_tools_are_gated():
    """`produces_proposals` is not a suggestion the planner gets to weigh."""
    rendered = render_catalogue(_registry())

    assert "produces_proposals" not in rendered.casefold()


def test_the_catalogue_tells_the_planner_a_proposal_needs_approving():
    """Not the flag — the consequence, in words it can plan around.

    A planner that thinks `propose_calendar_event` puts something on a calendar
    will tell the person it is done, and they will find out otherwise when the
    reminder never arrives.
    """
    rendered = render_catalogue(_registry())

    assert "approve" in rendered.casefold()


def test_the_planner_cannot_supply_a_timezone_that_would_be_dropped():
    """`normalize_action_time` refuses a timezone from model output.

    `create_proposals_from_candidates` passes `trusted_timezone=None`, so a
    timezone on the candidate is validated and then silently discarded on the
    way to the row — the worst kind of parameter, because it looks like it
    works. The owner supplies it at approval, where `resolved_timezone` is in
    the correction allowlist.
    """
    assert "timezone" not in _registry()["propose_calendar_event"].schema.model_fields


def test_the_planner_cannot_choose_a_reminder_lead_time():
    """Nobody writes how much warning they want in a chat message.

    Offering the field would only let the model invent one; it is asked for at
    the approval step, where somebody is already looking at the proposal.
    """
    fields = _registry()["propose_calendar_event"].schema.model_fields

    assert "reminder_minutes_before" not in fields
