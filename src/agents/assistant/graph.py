"""The Assistant Agent: a planner-executor state machine with a human gate.

```
START -> check_consent -> [not granted]   -> ask_permission -> END
                       -> load_memory -> plan -> route
route -> clarify                          -> END
route -> run_tools -> [made proposals]    -> human_confirm -> execute -> respond
                   -> [budget left]       -> plan   (at most MAX_REPLANS times)
                   -> [done]              -> respond -> END
route -> [nothing to do]                  -> respond -> END
```

Planner-executor rather than ReAct, for the reason ADR-05 already gives for the
Translation Agent, and ADR-40 restates for this one: the flow has a mandatory
human gate, and a gate can only be mandatory if the set of things needing gating
is fixed in code. `plan` emits tool calls, `run_tools` dispatches them through a
registry that is a dict in this repository — not whatever a provider's
function-calling decided to emit — and `produces_proposals` is a property of the
registration rather than of the model's mood.

Multi-step, but bounded. The planner sees what the tools returned and may ask
for more, at most `MAX_REPLANS` times; each round is a model call the person is
waiting through, and a planner that can always ask for one more tool will.

Nothing the assistant does reaches a calendar directly. A tool that would change
one writes an `action_proposals` row instead, so every change goes through the
one gate a detected commitment already goes through — which is also what lets
the person approving *annotate*: correct the time, or say how far ahead they
want to be nudged. A direct write has nothing to annotate; by the time they see
it, it has happened.

Every node takes the full ``AssistantState`` and returns a dict holding only the
fields it changed; LangGraph merges those partial updates. That convention is
stated here once rather than repeated in each node's docstring, matching
`src/agents/nodes/translation.py`.

Hard rule, inherited from the Translation Agent: **no node raises**. A failure is
recorded in ``state["error"]`` and the flow continues to `respond`, which can
still say something useful. An assistant that throws into the request would turn
a bad LLM day into a broken chat feature.

`human_confirm` calls LangGraph's ``interrupt()``, which suspends the run until
the caller resumes it with ``Command(resume=...)``. The suspension itself lives
in the checkpointer and is therefore process-local (ADR-32); what survives a
restart is the `action_proposals` rows the node wrote before suspending, which
carry everything `execute` needs. The graph is the computation, the database is
the truth — the same division the Translation Agent draws.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.assistant.guardrails import (
    cap_memory,
    cap_request,
    drop_repeated_calls,
    leaks_private_text,
)
from src.agents.assistant.prompts import (
    ANSWER_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    build_answer_user_prompt,
    build_planner_user_prompt,
)
from src.agents.assistant.state import MAX_REPLANS, AssistantState, PlannedStep
from src.services.assistant.agent_consent import ConsentRequiredError, require_consent

logger = logging.getLogger(__name__)

# How much recent conversation `load_memory` recalls. Wider than a translation's
# three-to-five lines because a summary is about a stretch of exchanges, and
# bounded because the whole transcript would not fit a prompt or a budget.
MEMORY_LIMIT = 30


def _note(state: AssistantState, **entries: Any) -> dict[str, Any]:
    """Shallow-merge into telemetry, which has no LangGraph reducer.

    Returning the dict bare would replace the whole thing and lose whatever
    earlier nodes recorded — the same trap `state["telemetry"]` has in the
    Translation Agent.
    """
    merged = dict(state.get("telemetry") or {})
    merged.update(entries)
    return merged


def make_check_consent(db: AsyncSession) -> Callable[[AssistantState], Awaitable[dict[str, Any]]]:
    """Build the node that refuses to start without the user's permission."""

    async def check_consent(state: AssistantState) -> dict[str, Any]:
        """Verify the caller granted `read_conversations` before anything reads.

        Answers inside the graph rather than raising. The caller asked the
        assistant a question and deserves a sentence back, not a 403 to
        translate — and the REST surface still refuses independently, because
        `ConversationIntelligenceService` checks the same scope itself.
        """
        try:
            await require_consent(db, state["user_id"], "read_conversations")
        except ConsentRequiredError as exc:
            return {"missing_consent": exc.scope, "telemetry": _note(state, consent="missing")}
        return {"missing_consent": None, "telemetry": _note(state, consent="granted")}

    return check_consent


def make_load_memory(
    db: AsyncSession,
    *,
    limit: int = MEMORY_LIMIT,
) -> Callable[[AssistantState], Awaitable[dict[str, Any]]]:
    """Build the node that recalls what the conversation has already said."""

    async def load_memory(state: AssistantState) -> dict[str, Any]:
        """Recall the conversation, by clock and by meaning.

        Two passes because they answer different questions. The time-ordered
        window is what "what did I miss" needs and is always right about
        recency. Semantic recall is what "what did we decide about the deadline"
        needs, and it reaches past the window into things said weeks ago that no
        limit would have included.

        Goes through `ChatService.get_message_history` for the first pass, which
        applies both the membership check and the visibility filter, so this
        node cannot become a second way into messages that skips either.

        The second pass has **no per-reader filter, and needs none**, which is
        worth stating plainly because it looks like an omission. A chunk may
        gather six messages, so it cannot be filtered per reader after the fact
        — half a chunk is not something retrieval can return. Private messages
        are therefore kept out of `assistant_chunks` entirely at write time
        (`assistant_indexing.load_source_messages` uses `public_only()`), which
        is the same call the translation agent's context window makes and the
        only filter that composes with chunking. Membership is still enforced,
        by the first pass, before this one runs.

        Semantic recall is additive: it is skipped when the user has not granted
        `store_memory`, when nothing is indexed yet, or when the provider is
        down, and in each case the node still returns the recent window.
        """
        from src.services.assistant.agent_consent import has_consent
        from src.services.assistant.assistant_retrieval import retrieve
        from src.services.messaging.chat import ChatService

        try:
            messages = await ChatService(db).get_message_history(
                user_id=state["user_id"],
                conversation_id=state["conversation_id"],
                limit=limit,
            )
        except Exception as exc:
            logger.warning("Assistant memory load failed", exc_info=True)
            return {"memory": [], "error": str(exc), "telemetry": _note(state, memory_lines=0)}

        usable = [
            message
            for message in messages
            if message.deleted_at is None and message.original_text.strip()
        ]
        recent_ids = {message.id for message in usable}

        # Retrieval goes through `assistant_chunks`, not through
        # `message_embeddings` (ADR-37). The two are not variants of one idea:
        # this searches chunks that may gather six messages or split one, with
        # the assistant's own embedding model, a lexical arm beside the vector
        # one, and a reranker — none of which the translation path can afford on
        # the request line.
        #
        # No `RAG_CONTEXT_ENABLED` here. For the assistant, retrieval is the
        # feature rather than an optimisation; the only gate is the user's
        # `store_memory` consent, which is a permission and not a switch.
        recalled_lines: list[str] = []
        cited: set[str] = set()
        if await has_consent(db, state["user_id"], "store_memory"):
            for chunk in await retrieve(
                db,
                conversation_id=state["conversation_id"],
                query_text=state.get("request_text", ""),
            ):
                # A chunk whose messages are all already in the recent window
                # would repeat that stretch of the transcript, which reads to
                # the model as it having been said twice.
                if set(chunk.message_ids) <= recent_ids:
                    continue
                recalled_lines.append(chunk.text)
                cited.update(chunk.message_ids)

        # Retrieved chunks first, then the recent window in order. Chunks carry
        # their own speaker labels and cover stretches that may be weeks apart,
        # so they cannot be interleaved into one timeline the way single
        # messages could — sorting them by their first message would put a
        # three-message chunk in the position of its earliest line and imply a
        # continuity that is not there.
        lines = cap_memory(
            [*recalled_lines, *(message.original_text for message in usable)]
        )
        return {
            "memory": lines,
            # What the leak check compares a generated summary against. Only the
            # private messages: everything else in this window is text the whole
            # conversation can already see.
            "private_texts": [
                message.original_text
                for message in usable
                if getattr(message, "visibility", "public") == "private"
            ],
            "telemetry": _note(
                state,
                memory_lines=len(lines),
                memory_recalled=len(recalled_lines),
                memory_cited=len(cited),
            ),
        }

    return load_memory


def make_plan(
    db: AsyncSession,
    llm_factory: Callable[[], Any] | None = None,
) -> Callable[[AssistantState], Awaitable[dict[str, Any]]]:
    """Build the planning node.

    Args:
        db: Session, needed to work out which tools this person has permitted.
        llm_factory: Model source; defaults to the configured provider. Injected
            so tests decide what the planner returns without reaching a network.
    """

    async def plan(state: AssistantState) -> dict[str, Any]:
        """Choose the next tool calls, or decide enough has been done.

        Runs more than once per request. On a replan it sees what the tools
        returned, which is the whole point: a second decision made from the same
        information as the first is a retry, not a plan.

        A failure here is not fatal. The planner falls back to summarising,
        which needs the least from the request and is almost always a defensible
        answer to "help me with this conversation".
        """
        from src.agents.observability import build_runnable_config
        from src.agents.tools.registry import (
            available_tools,
            build_registry,
            render_catalogue,
        )
        from src.services.shared.llm import extract_text, get_assistant_llm

        registry = await available_tools(
            db,
            user_id=state["user_id"],
            registry=build_registry(
                db,
                conversation_id=state["conversation_id"],
                user_id=state["user_id"],
            ),
        )
        if not registry:
            # Every tool needs a permission this person has not granted. Saying
            # so is better than planning against an empty catalogue and then
            # answering with nothing.
            return {
                "plan": [],
                "missing_consent": "read_conversations",
                "telemetry": _note(state, planner="no_tools"),
            }

        observations = list(state.get("observations") or [])
        replan_count = state.get("replan_count", 0)

        # The Assistant Agent's own model, not the translation one (ADR-39).
        factory = llm_factory or get_assistant_llm
        prompt = build_planner_user_prompt(
            request_text=cap_request(state.get("request_text", "")),
            has_memory=bool(state.get("memory")),
            observations=observations,
            replans_left=max(MAX_REPLANS - replan_count - 1, 0),
        )
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            # Traced like every other model call in this codebase. It was the
            # one that was not: the executors reach the backend through
            # `ConversationIntelligenceService`, so the planner was the only
            # stage of the assistant with no span and no token cost — which is
            # exactly the stage whose behaviour is hardest to reason about from
            # the outside.
            response = await factory().ainvoke(
                [
                    SystemMessage(
                        content=PLANNER_SYSTEM_PROMPT.format(
                            tool_catalogue=render_catalogue(registry)
                        )
                    ),
                    HumanMessage(content=prompt),
                ],
                config=build_runnable_config(
                    operation="assistant_plan",
                    conversation_id=state.get("conversation_id"),
                    user_id=state.get("user_id"),
                    replan=replan_count,
                ),
            )
            steps, clarification, done = _parse_plan(
                extract_text(response), known_tools=set(registry)
            )
        except Exception as exc:
            logger.warning("Assistant planning failed; defaulting to summarize", exc_info=True)
            return {
                "plan": [
                    {
                        "tool": "summarize_conversation",
                        "arguments": {},
                        "reason": "planner unavailable",
                    }
                ]
                if "summarize_conversation" in registry
                else [],
                "error": str(exc),
                "replan_count": replan_count + 1,
                "telemetry": _note(state, planner="fallback"),
            }

        # A planner that asks for a call it has already made has stopped
        # planning: the answer is in its own context, and re-running it spends a
        # round to learn nothing. Dropped per step rather than refusing the whole
        # plan, because a plan is often stale about its first step and right
        # about its second.
        already = [
            {"tool": observation["tool"], "arguments": {}}
            for observation in observations
        ]
        return {
            "plan": [] if done else drop_repeated_calls(steps, already),
            "clarification": clarification,
            "replan_count": replan_count + 1,
            "telemetry": _note(
                state,
                planner="ok",
                planned_steps=0 if done else len(steps),
                replans=replan_count + 1,
            ),
        }

    return plan


def _parse_plan(
    raw: str, *, known_tools: set[str]
) -> tuple[list[PlannedStep], str | None, bool]:
    """Read the planner's JSON, discarding anything this graph cannot dispatch.

    Unknown tool names are dropped rather than passed on. `run_tools` looks each
    one up in the registry, so a hallucinated name would reach nothing and the
    run would finish looking successful with nothing done — the failure mode
    that is hardest to notice.

    Returns:
        The steps to run, a question to ask instead, and whether the planner
        considers the request already answered. Unparseable output returns no
        steps and ``done`` false, which the caller treats as "nothing more to
        do" — silence rather than an invented action.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("Assistant planner returned unparseable JSON")
        return [], None, False

    if not isinstance(payload, dict):
        return [], None, False

    steps: list[PlannedStep] = []
    for item in payload.get("steps", []) or []:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        if tool not in known_tools:
            logger.warning("Assistant planner proposed unknown tool %r", tool)
            continue
        arguments = item.get("arguments")
        steps.append(
            {
                "tool": tool,
                "arguments": arguments if isinstance(arguments, dict) else {},
                "reason": str(item.get("reason", "")),
            }
        )

    clarification = payload.get("clarification")
    if not isinstance(clarification, str) or not clarification.strip():
        clarification = None

    return steps, clarification, bool(payload.get("done"))


def make_run_tools(db: AsyncSession) -> Callable[[AssistantState], Awaitable[dict[str, Any]]]:
    """Build the node that carries out the planned tool calls."""

    async def run_tools(state: AssistantState) -> dict[str, Any]:
        """Run every planned call, recording what each returned.

        Runs the whole plan rather than one step at a time. The planner already
        put them in order and they share one session; stopping after each to ask
        again would spend a model call to learn something the plan already said.
        The replan loop exists for when a result changes what should happen
        next, not to sequence steps the planner had already sequenced.

        Never raises: `call_tool` turns every failure into an observation the
        planner can act on. The alternative is an exception that ends the run,
        after which the person gets neither an answer nor an explanation.
        """
        from src.agents.tools.registry import build_registry, call_tool

        registry = build_registry(
            db,
            conversation_id=state["conversation_id"],
            user_id=state["user_id"],
        )
        source_message_id = (state.get("telemetry") or {}).get("source_message_id") or ""

        observations = list(state.get("observations") or [])
        proposals = list(state.get("proposals") or [])
        summary = state.get("summary")
        gated = False

        for step in state.get("plan") or []:
            spec = registry.get(step["tool"])
            if spec is None:
                continue

            # The tools that need one are handed the triggering message rather
            # than allowed to name it. A plan that could choose a message id
            # could point the extractor at something the person never mentioned.
            bound: dict[str, Any] = {}
            if step["tool"] in ("extract_actions", "propose_calendar_event"):
                bound["source_message_id"] = source_message_id

            result = await call_tool(spec, step.get("arguments") or {}, **bound)
            observations.append(
                {"tool": result.tool, "ok": result.ok, "summary": result.summary}
            )

            if not result.ok:
                continue
            if spec.produces_proposals and isinstance(result.data, list):
                proposals.extend(result.data)
                gated = gated or bool(result.data)
            if result.tool == "summarize_conversation" and isinstance(result.data, dict):
                # The last check before generated text can reach a group. The
                # transcript this was built from is what the *caller* may read,
                # which includes the assistant's own private replies to them; a
                # summary quoting one verbatim would show it to everybody
                # (ADR-31). Dropped rather than redacted: a summary with a hole
                # in it is not a summary, and the honest move is to say nothing
                # rather than something subtly wrong.
                if leaks_private_text(
                    result.data.get("summary", ""), state.get("private_texts") or []
                ):
                    logger.warning(
                        "Discarded an assistant summary that quoted a private message"
                    )
                    observations[-1] = {
                        "tool": result.tool,
                        "ok": False,
                        "summary": "The summary could not be used.",
                    }
                    continue
                summary = result.data

        return {
            # Emptied, so a non-empty plan later means the planner asked for
            # something new rather than that these were forgotten.
            "plan": [],
            "observations": observations,
            "proposals": proposals,
            "summary": summary,
            "telemetry": _note(
                state,
                tool_calls=len(state.get("plan") or []),
                observations=len(observations),
                gated=gated,
            ),
        }

    return run_tools


def route_after_plan(state: AssistantState) -> str:
    """Send the run to whatever the plan asks for next."""
    if state.get("missing_consent"):
        return "ask_permission"
    if state.get("clarification"):
        return "clarify"
    if state.get("plan"):
        return "run_tools"
    return "respond"


def route_after_tools(state: AssistantState) -> str:
    """Decide whether to stop, ask a human, or plan again.

    The gate comes first and unconditionally. Anything that produced proposals
    stops here for approval before the planner gets another turn — otherwise a
    replan could pile a second proposal on top of one nobody has looked at, and
    the person would be approving a list they never saw assembled.
    """
    if state.get("proposals"):
        return "human_confirm"
    if state.get("replan_count", 0) < MAX_REPLANS:
        return "plan"
    return "respond"


def route_after_consent(state: AssistantState) -> str:
    """Stop before reading anything when the permission is not there."""
    return "ask_permission" if state.get("missing_consent") else "load_memory"


def clarify(state: AssistantState) -> dict[str, Any]:
    """Ask the person the question the planner could not answer for itself.

    A successful outcome, not a failure. The alternative — picking one of two
    meetings and moving it — is the worst result available here, because the
    person will not know a choice was made on their behalf until they miss
    something.
    """
    return {
        "reply": state.get("clarification") or "",
        "telemetry": _note(state, outcome="clarify"),
    }


def human_confirm(state: AssistantState) -> dict[str, Any]:
    """Suspend the run until a person approves or rejects each proposal.

    This is the constraint the assignment calls mandatory and `NewFeature.md`
    §1.3 draws with no path around it: nothing reaches a calendar without a
    human saying so.

    The proposals are already persisted as `pending_confirmation` rows before
    this point, which is what makes the pause safe to lose. If the process
    restarts while suspended, the run is gone but the rows are not, and the
    confirmation endpoint executes from them directly (ADR-32).

    ``interrupt()`` raises internally to suspend, so the return statement below
    only runs on resume, carrying whatever the caller passed to
    ``Command(resume=...)``.
    """
    decision = interrupt(
        {
            "kind": "confirm_proposals",
            "proposals": state.get("proposals", []),
        }
    )
    if not isinstance(decision, dict):
        return {"approved_proposal_ids": [], "proposal_annotations": {}}

    # Approving is where the person supplies what the message never contained.
    # Somebody writes "review thiết kế 10h sáng thứ Tư"; nobody writes how much
    # warning they want, or notices the extractor read the wrong Wednesday. Both
    # corrections ride in here, keyed by proposal, and reach `confirm_proposal`
    # unchanged.
    annotations = decision.get("annotations")
    return {
        "approved_proposal_ids": list(decision.get("approved_proposal_ids", []) or []),
        "proposal_annotations": annotations if isinstance(annotations, dict) else {},
    }


def make_execute(db: AsyncSession) -> Callable[[AssistantState], Awaitable[dict[str, Any]]]:
    """Build the node that carries out what the human approved."""

    async def execute(state: AssistantState) -> dict[str, Any]:
        """Confirm each approved proposal, leaving the rest untouched.

        Silence is a decision: a proposal the human did not name stays
        `pending_confirmation` rather than being rejected, because not answering
        is not the same as saying no.
        """
        from src.services.intelligence.action_proposals import (
            ActionProposalError,
            ActionProposalService,
        )

        service = ActionProposalService(db)
        annotations = state.get("proposal_annotations") or {}
        executed: list[dict[str, Any]] = []
        for proposal_id in state.get("approved_proposal_ids", []):
            annotation = dict(annotations.get(proposal_id) or {})
            # The lead time shapes the calendar entry rather than the proposal
            # row, so it travels as its own argument. Left among the corrections
            # it would be dropped by the allowlist in `confirm_proposal` and the
            # person's choice would vanish with no error.
            reminder = annotation.pop("reminder_minutes_before", 15)
            try:
                confirmed = await service.confirm_proposal(
                    proposal_id,
                    state["user_id"],
                    corrections=annotation or None,
                    reminder_minutes_before=reminder,
                )
            except ActionProposalError as exc:
                # One proposal failing must not abandon the others: they are
                # separate promises the user made and approved separately.
                logger.warning("Confirming proposal %s failed: %s", proposal_id, exc)
                continue
            executed.append({"id": confirmed.id, "title": confirmed.title})

        return {"executed": executed, "telemetry": _note(state, executed_count=len(executed))}

    return execute


def ask_permission(state: AssistantState) -> dict[str, Any]:
    """Explain which permission is missing, in words rather than a status code."""
    scope = state.get("missing_consent") or "read_conversations"
    return {
        "reply": (
            "Mình cần bạn cho phép trước đã. Hãy bật quyền tương ứng trong phần "
            f"Cài đặt → Trợ lý ({scope}), rồi nhờ mình lại nhé."
        )
    }


def make_respond(
    llm_factory: Callable[[], Any] | None = None,
) -> Callable[[AssistantState], Awaitable[dict[str, Any]]]:
    """Build the node that turns whatever the run produced into one message."""

    async def respond(state: AssistantState) -> dict[str, Any]:
        """Answer, from what the run actually found.

        Reached from every terminal path, including the failing ones, so that a
        run that went wrong still says something instead of returning nothing.

        The branches are ordered by how *specific* the material is, not by how
        good it is. An executed action is the most concrete thing that can have
        happened; a synthesised answer is the least, and comes last among the
        real answers because anything above it is already the thing the person
        asked for.
        """
        if state.get("reply"):
            return {}

        executed = state.get("executed") or []
        if executed:
            titles = ", ".join(item["title"] for item in executed)
            return {"reply": f"Đã thêm vào lịch của bạn: {titles}."}

        proposals = state.get("proposals") or []
        if proposals:
            return {
                "reply": (
                    f"Mình tìm thấy {len(proposals)} việc cần làm trong hội thoại này. "
                    "Bạn xem lại rồi duyệt giúp mình nhé."
                )
            }

        summary = state.get("summary")
        if summary and summary.get("summary"):
            return {"reply": summary["summary"]}

        observations = [
            observation
            for observation in (state.get("observations") or [])
            if observation.get("ok")
        ]
        if observations:
            answer = await _answer_from(
                state, observations=observations, llm_factory=llm_factory
            )
            if answer:
                return {"reply": answer, "telemetry": _note(state, outcome="answered")}

        if state.get("error"):
            return {
                "reply": (
                    "Mình chưa xử lý được yêu cầu này ngay lúc này. "
                    "Bạn thử lại, hoặc nói rõ hơn điều bạn muốn mình hỗ trợ."
                )
            }

        return {"reply": "Bạn muốn mình hỗ trợ điều gì trong cuộc trò chuyện này?"}

    return respond


async def _answer_from(
    state: AssistantState,
    *,
    observations: list[dict[str, Any]],
    llm_factory: Callable[[], Any] | None,
) -> str:
    """Compose one answer from what the tools returned, or return nothing.

    This is the stage that makes a question answerable at all. Without it a run
    that searched successfully would fall through to the generic prompt, and the
    person would be told the search found nothing when it had found exactly what
    they asked for.

    It is also where a `negative` question is answered correctly. The prompt's
    load-bearing instruction is to say plainly that the conversation does not
    contain the answer rather than offer the nearest thing found — which is what
    retrieval always returns, because a vector index returns *something* for
    every query.

    Returns the empty string on any failure. `respond` then falls through to its
    generic reply, which is a worse answer rather than no answer at all.
    """
    from src.agents.observability import build_runnable_config
    from src.services.shared.llm import extract_text, get_assistant_llm

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        factory = llm_factory or get_assistant_llm
        response = await factory().ainvoke(
            [
                SystemMessage(content=ANSWER_SYSTEM_PROMPT),
                HumanMessage(
                    content=build_answer_user_prompt(
                        request_text=cap_request(state.get("request_text", "")),
                        observations=observations,
                    )
                ),
            ],
            config=build_runnable_config(
                operation="assistant_answer",
                conversation_id=state.get("conversation_id"),
                user_id=state.get("user_id"),
            ),
        )
        answer = extract_text(response).strip()
    except Exception:
        logger.warning("Assistant answer synthesis failed", exc_info=True)
        return ""

    # The same check the summary path gets. This text is built from the recent
    # window as well as from retrieval, and the window returns what the *caller*
    # may read — including the assistant's own private replies to them (ADR-31).
    if leaks_private_text(answer, state.get("private_texts") or []):
        logger.warning("Discarded an assistant answer that quoted a private message")
        return ""
    return answer


def build_assistant_graph(
    *,
    db: AsyncSession,
    checkpointer: Any,
    llm_factory: Callable[[], Any] | None = None,
) -> Any:
    """Compile the assistant graph with its dependencies bound.

    Built per request rather than once at import, because every node needs the
    caller's database session. The Translation Agent's module-level ``agent`` is
    the exception there, not the rule — its own docstring says the chat service
    should build its own graph with a real provider.

    Args:
        db: Session the nodes read and write through.
        checkpointer: Required. ``interrupt()`` cannot suspend without one, so a
            graph compiled without it would run `human_confirm` straight through
            and approve nothing while looking like it worked.
        llm_factory: Model source for the planner; defaults to the configured
            provider.

    Returns:
        The compiled graph, ready for ``ainvoke`` with a thread id in its config.
    """
    graph = StateGraph(AssistantState)

    graph.add_node("check_consent", make_check_consent(db))
    graph.add_node("load_memory", make_load_memory(db))
    graph.add_node("plan", make_plan(db, llm_factory))
    graph.add_node("run_tools", make_run_tools(db))
    graph.add_node("clarify", clarify)
    graph.add_node("human_confirm", human_confirm)
    graph.add_node("execute", make_execute(db))
    graph.add_node("ask_permission", ask_permission)
    graph.add_node("respond", make_respond(llm_factory))

    graph.add_edge(START, "check_consent")
    # Path maps on every conditional edge, for the reason the Translation Agent
    # states: without them the compiled graph shows these nodes as terminal, and
    # compile() cannot check a router's return values against real node names.
    graph.add_conditional_edges(
        "check_consent",
        route_after_consent,
        {"ask_permission": "ask_permission", "load_memory": "load_memory"},
    )
    graph.add_edge("load_memory", "plan")
    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {
            "run_tools": "run_tools",
            "clarify": "clarify",
            "respond": "respond",
            "ask_permission": "ask_permission",
        },
    )
    # The only cycle in the graph, and the only one there should be. It is
    # bounded by `replan_count` in `route_after_tools` rather than by anything
    # the model controls: a planner asked to count its own turns will miscount,
    # and the budget is what stands between a confused run and an unbounded one.
    graph.add_conditional_edges(
        "run_tools",
        route_after_tools,
        {
            "human_confirm": "human_confirm",
            "plan": "plan",
            "respond": "respond",
        },
    )
    graph.add_edge("human_confirm", "execute")
    graph.add_edge("execute", "respond")
    graph.add_edge("clarify", END)
    graph.add_edge("ask_permission", END)
    graph.add_edge("respond", END)

    return graph.compile(checkpointer=checkpointer)
