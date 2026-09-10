"""What a translation needs to know about who it is for.

The agent never queries a database (ADR-01). Conversation context already
reaches it through `ContextProvider`; this is the same arrangement for the
second thing a translation depends on — the subject area a conversation is
about and who it is with, which together decide whether "UI" stays "UI" or
becomes "giao diện".

A Protocol rather than an import, so `src/agents/` keeps depending on nothing
below it. The database-backed implementation lives in `src/services/`, and the
evaluation harness supplies its own without a database at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class GlossaryTerm:
    """One term the translation is required to render a fixed way.

    Unused until the glossary lookup lands. Declared here now so the provider
    contract does not have to change under callers that already implement it.
    """

    source_term: str
    target_term: str
    keep_verbatim: bool = False


@dataclass(frozen=True)
class Customization:
    """Everything known about the audience for one translation.

    Every field is optional and empty is meaningful: it is the state of every
    conversation until enough messages exist to infer anything from, so the
    prompt has to read exactly as it did before this existed rather than fill
    with hedging.
    """

    domain: str = ""
    audience: str = ""
    glossary_terms: tuple[GlossaryTerm, ...] = field(default_factory=tuple)


class CustomizationProvider(Protocol):
    """Source of the audience facts for one translation.

    `original_text` and `source_language` are part of the signature although
    nothing reads them yet: the glossary lookup that follows selects terms by
    what the message actually says, and widening a Protocol afterwards means
    editing every implementation, including the ones in tests.
    """

    async def get_customization(
        self,
        conversation_id: str,
        *,
        original_text: str,
        source_language: str,
        target_language: str,
    ) -> Customization: ...


class NullCustomizationProvider:
    """Knows nothing, which is a valid answer and not a failure.

    The default for a graph built without one — the compiled instance in
    `graph.py`, and any caller that has no conversation to look up.
    """

    async def get_customization(
        self,
        conversation_id: str,
        *,
        original_text: str,
        source_language: str,
        target_language: str,
    ) -> Customization:
        """Return the empty customization."""
        return Customization()
