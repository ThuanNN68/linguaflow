"""Run the Assistant Agent graph, and hold its suspended runs.

The checkpointer is a module-level `InMemorySaver`. It accelerates a run on the
instance that created it, but durable proposals remain the authority across
replicas; confirmation endpoints execute from those rows rather than requiring
an in-memory graph resume.

What a restart costs is bounded, because the graph is not where the durable
state lives. `human_confirm` suspends only after its proposals are already
`pending_confirmation` rows carrying title, time, timezone and owner. Lose the
suspension and the rows remain; the confirmation endpoint then executes from
them directly. One implementation of the effect (`confirm_proposal`), two places
that trigger it — which is ordinary, not duplication.

ADR-32 records why this beat `langgraph-checkpoint-postgres`: that package
brings a second Postgres driver (psycopg) alongside asyncpg and creates four
tables at run time under its own migration ledger, which contradicts ADR-06's
rule that Alembic is the only thing that defines schema.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.assistant import build_assistant_graph
from src.services.assistant.assistant_telemetry import record_attempt

logger = logging.getLogger(__name__)

# One saver for the process. Threads are keyed by a uuid handed back to the
# caller, so two users' runs never collide even though they share the store.
_CHECKPOINTER = InMemorySaver()

def _run_config(thread_id: str, **metadata: Any) -> dict[str, Any]:
    """The config one graph invocation runs under: checkpoint thread and tracing.

    LangGraph takes a single config, so the two have to be merged here rather
    than passed separately. `build_runnable_config` returns the tracing half —
    an empty dict when no backend is configured or its key is missing — and the
    thread id is added on top, so a deployment with tracing switched off behaves
    exactly as it did before.
    """
    from src.agents.observability import build_runnable_config

    config = dict(build_runnable_config(**metadata) or {})
    config.setdefault("configurable", {})["thread_id"] = thread_id
    return config


# Which account owns which suspended run. Without this a caller could resume
# somebody else's thread by guessing its id, and `execute` would then confirm
# proposals under the wrong user — `confirm_proposal` would refuse on ownership,
# but relying on the layer below to catch an authorization hole is how holes
# survive refactors.
_THREAD_OWNERS: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class AssistantRunResult:
    """What one turn of the assistant produced."""

    reply: str
    proposals: list[dict[str, Any]] = field(default_factory=list)
    # Present only while the run is parked at `human_confirm`. `None` means the
    # run finished, so there is nothing to resume and nothing to approve.
    thread_id: str | None = None
    executed: list[dict[str, Any]] = field(default_factory=list)


class AssistantThreadNotFoundError(Exception):
    """The run is not suspended here — restarted, resumed, or never existed."""


class AssistantThreadOwnershipError(Exception):
    """The caller does not own the suspended run they asked to resume."""


class AssistantAgentService:
    """Drive the assistant graph for one database session."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def run(
        self,
        *,
        conversation_id: str,
        user_id: str,
        request_text: str,
        source_message_id: str | None = None,
    ) -> AssistantRunResult:
        """Answer one request, stopping at the human gate if there is one.

        Args:
            conversation_id: Conversation the request is about.
            user_id: The authenticated caller. Every permission and ownership
                decision downstream keys off this, never off anything the
                message text claims.
            request_text: What the user asked, untrusted.
            source_message_id: The message actions should be extracted from.
                Absent for a request that only needs a summary.

        Returns:
            The reply, any proposals awaiting approval, and the thread id needed
            to resume — `None` when the run completed.
        """
        thread_id = str(uuid.uuid4())
        graph = build_assistant_graph(db=self._db, checkpointer=_CHECKPOINTER)
        started = time.perf_counter()
        state = await graph.ainvoke(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "request_text": request_text,
                "telemetry": {"source_message_id": source_message_id},
            },
            # The thread id and the trace callback travel in the same config.
            # Passing only the first is what left the whole assistant graph
            # untraced while its two executors, which build their own config,
            # appeared in the backend — so the spans that existed described the
            # work and never the run that ordered it.
            _run_config(
                thread_id,
                conversation_id=conversation_id,
                user_id=user_id,
                stage="run",
            ),
        )

        await record_attempt(
            self._db,
            state=state,
            conversation_id=conversation_id,
            user_id=user_id,
            total_ms=int((time.perf_counter() - started) * 1000),
        )

        if "__interrupt__" in state:
            _THREAD_OWNERS[thread_id] = user_id
            return AssistantRunResult(
                reply=state.get("reply") or "",
                proposals=state.get("proposals") or [],
                thread_id=thread_id,
            )

        return AssistantRunResult(
            reply=state.get("reply") or "",
            proposals=state.get("proposals") or [],
            executed=state.get("executed") or [],
        )

    async def resume(
        self,
        *,
        thread_id: str,
        user_id: str,
        approved_proposal_ids: list[str],
        annotations: dict[str, dict[str, Any]] | None = None,
    ) -> AssistantRunResult:
        """Continue a run parked at `human_confirm` with the person's answer.

        Args:
            annotations: Per-proposal corrections the person made at the gate —
                a fixed time, a different title, how far ahead to be reminded —
                keyed by proposal id. This is what a direct calendar write could
                never offer: by the time somebody saw it, it would already have
                happened.

        Raises:
            AssistantThreadNotFoundError: Nothing is suspended under that id.
                Expected after a restart, and the caller should fall back to
                confirming the proposals through the REST endpoint.
            AssistantThreadOwnershipError: The run belongs to another account.
        """
        owner = _THREAD_OWNERS.get(thread_id)
        if owner is None:
            raise AssistantThreadNotFoundError(thread_id)
        if owner != user_id:
            raise AssistantThreadOwnershipError(thread_id)

        graph = build_assistant_graph(db=self._db, checkpointer=_CHECKPOINTER)
        started = time.perf_counter()
        state = await graph.ainvoke(
            Command(
                resume={
                    "approved_proposal_ids": approved_proposal_ids,
                    "annotations": annotations or {},
                }
            ),
            _run_config(thread_id, user_id=user_id, stage="resume"),
        )
        # A second row for the same request, not an update of the first. The
        # suspended half and the resumed half are two separate stretches of
        # waiting — one on a model, one on a person — and averaging them
        # together would report a latency nobody experienced.
        await record_attempt(
            self._db,
            state=state,
            conversation_id=state.get("conversation_id"),
            user_id=user_id,
            total_ms=int((time.perf_counter() - started) * 1000),
        )

        # The gate is answered once. Leaving the entry behind would let a second
        # resume run `execute` again on the same approvals.
        _THREAD_OWNERS.pop(thread_id, None)

        return AssistantRunResult(
            reply=state.get("reply") or "",
            proposals=state.get("proposals") or [],
            executed=state.get("executed") or [],
        )


def forget_suspended_runs() -> None:
    """Drop every suspended run. For tests, so one does not leak into the next."""
    _THREAD_OWNERS.clear()
