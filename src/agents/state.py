"""State schema for the Translation Agent.

Defined by docs/api/contract.md section 2. Field names are shared across Frontend,
Backend and Agent — do not rename them here without updating the contract first.
"""

from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """State passed between nodes of the translation graph.

    ``total=False`` lets each node return only the fields it changes; LangGraph
    merges those partial updates into the shared state.
    """

    # Identifiers supplied by the Chat Service
    conversation_id: str
    message_id: str
    sender_id: str

    # Input
    original_text: str
    source_language: str  # ISO 639-1; provisional on entry, overwritten by detection
    target_language: str  # Taken from users.preferred_language of the recipient
    # Which of `senior | peer | junior | client` this rendering addresses the
    # reader with. Half of the fan-out key, so one message is translated once
    # per (language, standing) rather than once per language: Vietnamese,
    # Japanese and Korean cannot form a sentence without choosing an address
    # form, and a manager and a client in one group are owed different ones.
    # `peer` is the neutral value and what an unprofiled conversation uses.
    honorific_profile: str
    sender_honorific_profile: str
    # Reader-selected rendering style. This is server-derived from settings,
    # never client prompt text.
    translation_tone: str

    # Conversation context
    context_messages: list[str]  # 3-5 recent messages, preformatted for the prompt
    # Written by the `customize` node from the conversation's inferred profile.
    # Empty means nothing has been inferred yet, which is every conversation
    # until it has enough messages — not an error, and the prompt simply omits
    # its Audience section (ADR-24).
    domain: str
    audience: str
    # Terms the translation must render a fixed way, selected for this message
    # by the `customize` node. Not part of the wire contract: no client reads
    # them, and they change with what the glossary holds (ADR-26).
    glossary_terms: list

    # Output
    translated_text: str
    translation_id: str  # Row id in translation_results, required by F-05
    is_valid: bool  # Result of the validate_output node
    is_fallback: bool  # True when the original text is returned after a failure

    # Measurements persisted to translation_results and reported to Langfuse
    model: str
    latency_ms: int

    error: str

    # Measurement only. The keys inside are NOT part of the contract: they are
    # written by the nodes, read by `record_attempt`, and free to change with
    # what the team wants to measure. One field rather than eight keeps a new
    # metric from being a contract change every time (docs/api/contract.md
    # section 2). No client may depend on anything in here.
    telemetry: dict
