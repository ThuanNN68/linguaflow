"""Conversation context sources for the Translation Agent.

The agent needs the most recent messages of a conversation to resolve pronouns
and keep the translation coherent across turns (docs/api/contract.md section 2,
ADR-01).

The source is abstracted behind the protocol below so this package never imports
`src/database/`. The production implementation reading the `messages` table is
`DatabaseContextProvider` in `src/services/messaging/context_provider.py`; it satisfies
this protocol structurally, which keeps the agent reusable on a different schema
or transport.
"""

from __future__ import annotations

from typing import Protocol

# Fallback window size when no explicit limit is given. The configured value
# lives in Settings.agent_context_size (the PRD specifies 3-5).
DEFAULT_CONTEXT_SIZE = 5


class ContextProvider(Protocol):
    """Supplies recent conversation history to the agent."""

    async def get_recent_messages(
        self,
        conversation_id: str,
        limit: int = DEFAULT_CONTEXT_SIZE,
    ) -> list[str]:
        """Return up to `limit` recent messages, oldest first.

        Args:
            conversation_id: Conversation whose history to read.
            limit: Maximum number of messages to return.

        Returns:
            Lines already formatted for inclusion in the prompt, oldest first.
            Empty when the conversation has no history.
        """
        ...


class NullContextProvider:
    """Supplies no context. Default when no real source is configured.

    Translation still works, it just loses conversation awareness.
    """

    async def get_recent_messages(
        self,
        conversation_id: str,
        limit: int = DEFAULT_CONTEXT_SIZE,
    ) -> list[str]:
        """Return an empty history.

        Both arguments are accepted to satisfy `ContextProvider` (which documents
        them) and then ignored — this implementation has nothing to look up.
        """
        return []


class InMemoryContextProvider:
    """Keeps context in process memory. For tests and demos only.

    Nothing survives a restart, so this must not be used in production.
    """

    def __init__(self, messages: dict[str, list[str]] | None = None) -> None:
        """Seed the store, optionally with pre-existing history per conversation."""
        self._store: dict[str, list[str]] = messages or {}

    def add_message(self, conversation_id: str, message: str) -> None:
        """Append a message to a conversation's history.

        Args:
            conversation_id: Conversation to append to; created if unseen.
            message: Line to store, already formatted for the prompt.
        """
        self._store.setdefault(conversation_id, []).append(message)

    async def get_recent_messages(
        self,
        conversation_id: str,
        limit: int = DEFAULT_CONTEXT_SIZE,
    ) -> list[str]:
        """Return the last `limit` messages stored for `conversation_id`.

        Args:
            conversation_id: Conversation whose history to read.
            limit: Maximum number of messages; zero or less returns nothing.

        Returns:
            Stored lines, oldest first. Empty when the conversation is unknown.
        """
        history = self._store.get(conversation_id, [])
        if limit <= 0:
            return []
        return history[-limit:]
