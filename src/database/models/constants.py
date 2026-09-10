"""Schema-level vocabularies and invariants shared between model domains."""

EMBEDDING_DIM = 768


HONORIFIC_PROFILES = ("senior", "peer", "junior", "client")


DEFAULT_HONORIFIC_PROFILE = "peer"


GLOSSARY_AUDIENCES = ("internal", "client")


GLOSSARY_DOMAINS = ("engineering", "commercial", "support")


TRANSLATION_TONES = ("natural", "formal", "casual", "friendly")


MESSAGE_TYPES = ("text", "voice")


TRANSCRIPTION_STATUSES = ("pending", "completed", "failed")


MESSAGE_LIFECYCLE_CHECK = (
    "(message_type = 'text' AND transcription_status IS NULL) OR "
    "(message_type = 'voice' AND transcription_status IS NOT NULL AND ("
    "(transcription_status IN ('pending', 'failed') AND original_text = '') OR "
    "(transcription_status = 'completed' AND length(trim(original_text)) > 0)"
    "))"
)


GLOSSARY_ENTRY_STATUSES = ("active", "retired")


GLOSSARY_PROPOSAL_STATUSES = ("pending", "approved", "rejected")


AGENT_CONSENT_SCOPES = (
    "read_conversations",
    "proactive_scan",
    "store_memory",
    "calendar_read",
    "calendar_write",
)


MESSAGE_VISIBILITIES = ("public", "private")


CALENDAR_EVENT_SOURCES = ("assistant", "manual")


CALENDAR_EVENT_STATUSES = ("active", "cancelled")


AGENT_CONSENT_POLICY_VERSION = "1"


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    """Render a CheckConstraint body from a tuple of allowed values.

    Keeps the tuple above as the single definition: a value added there reaches
    the database without anyone having to remember a second list, which is the
    mistake `ATTEMPT_OUTCOMES` avoids the same way.
    """
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


ATTEMPT_OUTCOMES = (
    "llm",  # the configured LLM translated and the output passed validation
    "secondary",  # deep-translator translated instead (ADR-07 tier 2)
    "original",  # no tier translated, the original text was delivered (tier 3)
    "passthrough",  # source language equalled target, no model was called
    "timeout",  # the run exceeded translation_timeout_seconds
    "error",  # the graph raised
    "empty",  # the graph returned an empty translation
)


ACTION_PROPOSAL_TYPES = ("task", "appointment")


ACTION_PROPOSAL_STATUSES = ("needs_clarification", "pending_confirmation", "confirmed", "rejected", "stale")


ACTION_PROPOSAL_SOURCE_MODES = ("on_demand", "proactive")


ASSISTANT_CHUNK_STRATEGIES = (
    # One message per chunk. The behaviour that measured hit@3 = 22% and the
    # baseline every other strategy has to beat.
    "message",
    # Consecutive messages grouped by speaker and time gap. For information a
    # person spread over five short messages.
    "turn_window",
    # Fixed token budget with overlap, splitting long messages. For an answer
    # buried in the middle of a two-thousand-character message.
    "token_window",
    # Cut where consecutive messages stop resembling each other. For topic
    # boundaries in a very long thread.
    "semantic_split",
    # Small children indexed, larger parents returned.
    "parent_child",
)


ASSISTANT_MEMORY_KINDS = ("preference", "fact", "relationship", "recurring")


ASSISTANT_OUTCOMES = (
    # Replied with an answer or a summary.
    "answered",
    # Stopped at the human gate with proposals nobody has looked at yet.
    "proposed",
    # Proposals were approved and carried out in the same run.
    "executed",
    # Asked the person a question instead of guessing.
    "clarified",
    # Stopped before reading anything: a permission was not granted.
    "refused",
    # Ran to the end with nothing to do or nothing found.
    "empty",
    # The run failed. `error_code` says how.
    "error",
)
