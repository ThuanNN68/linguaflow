"""Translation Agent — LangGraph state machine.

Flow per docs/architecture/diagrams.md section 2:

    START -> detect_language -> [source == target?]
                                  |-- yes   -> passthrough     -> END
                                  |-- error -> validate_output
                                  `-- no    -> build_context -> translate
                                                             -> validate_output

    validate_output -> [translation usable?]
                         |-- yes -> END
                         `-- no  -> fallback_translate -> END

The fallback branch asks the secondary provider (ADR-07) for a translation. If
that fails too, the original text validate_output already stored is what the
recipient receives.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.agents.context_provider import DEFAULT_CONTEXT_SIZE, ContextProvider
from src.agents.customization import CustomizationProvider
from src.agents.nodes.translation import (
    detect_language,
    fallback_translate,
    make_build_context,
    make_customize,
    passthrough,
    translate,
    validate_output,
)
from src.agents.state import AgentState
from src.config import get_settings


def route_after_detect(state: AgentState) -> str:
    """Pick the branch to take once the source language is known.

    - Detection failed: go straight to validate_output, which emits the fallback.
    - Source equals target: skip the LLM entirely, saving tokens and latency.
    - Otherwise: translate normally.
    """
    if state.get("error"):
        return "validate_output"

    source_language = state.get("source_language")
    target_language = state.get("target_language")
    if source_language and source_language == target_language:
        return "passthrough"

    return "build_context"


def route_after_validate(state: AgentState) -> str:
    """Send a failed translation to the secondary provider before giving up.

    validate_output has already put the original text into the state, so this
    branch can only improve the result, never lose the message (ADR-07).
    """
    if state.get("is_fallback"):
        return "fallback_translate"

    return END


def build_translation_graph(
    context_provider: ContextProvider | None = None,
    context_size: int | None = None,
    customization_provider: CustomizationProvider | None = None,
):
    """Build and compile the translation graph.

    Args:
        context_provider: source of the recent messages used as context.
            Defaults to no context (see src/agents/context_provider.py).
        context_size: how many recent messages to include. Defaults to the
            AGENT_CONTEXT_SIZE setting.
        customization_provider: source of the subject area and audience the
            translation is for. Defaults to knowing nothing, which renders the
            prompt exactly as it read before the node existed.
    """
    if context_size is None:
        if context_provider is None:
            # NullContextProvider returns nothing regardless of the limit, so
            # reading settings here would only load .env at import time for a
            # value this graph can never use.
            context_size = DEFAULT_CONTEXT_SIZE
        else:
            context_size = get_settings().agent_context_size

    graph = StateGraph(AgentState)

    graph.add_node("detect_language", detect_language)
    graph.add_node("build_context", make_build_context(context_provider, context_size))
    graph.add_node("customize", make_customize(customization_provider))
    graph.add_node("translate", translate)
    graph.add_node("validate_output", validate_output)
    graph.add_node("fallback_translate", fallback_translate)
    graph.add_node("passthrough", passthrough)

    graph.add_edge(START, "detect_language")
    # The path map is what puts the three branch edges into the compiled graph:
    # without it draw_mermaid() shows detect_language as terminating the flow,
    # and compile() cannot check the router's return values against node names.
    graph.add_conditional_edges(
        "detect_language",
        route_after_detect,
        {
            "passthrough": "passthrough",
            "build_context": "build_context",
            "validate_output": "validate_output",
        },
    )
    # Between context and translation on purpose: it needs no context but
    # must run before the prompt is built, and putting it here keeps the
    # happy path a straight line that reads in the order it executes.
    graph.add_edge("build_context", "customize")
    graph.add_edge("customize", "translate")
    graph.add_edge("translate", "validate_output")
    graph.add_conditional_edges(
        "validate_output",
        route_after_validate,
        {"fallback_translate": "fallback_translate", END: END},
    )
    graph.add_edge("fallback_translate", END)
    graph.add_edge("passthrough", END)

    return graph.compile()


# Default instance without conversation context. The Chat Service should build
# its own graph with a real ContextProvider rather than reuse this one.
#
# NOTE: src/api/routes.py imports the name `agent`. Do not rename it until the
# legacy /chat endpoint is removed (docs/api/contract.md section 3.3).
agent = build_translation_graph()
