"""Recording how each Assistant Agent run ended, for `make metrics` to read.

The only writer to `assistant_attempts`, exactly as `record_attempt` in
`src/services/language/translation.py` is the only writer to `translation_attempts`.
Keeping that to one function is what lets the table's columns change with what
needs measuring: there is one place to update, and nothing else in the codebase
depends on its shape (ADR-16).

Two properties are load-bearing.

**A row at every exit.** Including the endings that produce nothing — a refused
permission, a question asked back, a run that found nothing to do. Those are the
reason the table exists: "how often does the assistant reach the gate" cannot be
computed from the runs that reached it.

**Never able to fail a run.** Every failure here is logged and swallowed. A
person waiting on an answer must not lose it to a bookkeeping error, which is
the same rule NFR-02 sets for the translation path.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.database.models import ASSISTANT_OUTCOMES, AssistantAttempt

logger = logging.getLogger(__name__)


def classify(state: dict[str, Any]) -> tuple[str, str]:
    """Read the finished state and say how the run ended.

    Ordered by how specific each ending is, not by how good it is. `executed`
    before `proposed` because a run that carried something out also had
    proposals; `clarified` before `empty` because asking is an outcome and
    finding nothing is the absence of one.

    Returns:
        ``(outcome, error_code)``. The code is the consent scope for a refusal
        and the exception type for a failure — short and groupable, so a report
        counts causes rather than printing them.
    """
    if state.get("missing_consent"):
        return "refused", str(state["missing_consent"])
    if state.get("executed"):
        return "executed", ""
    if state.get("proposals"):
        return "proposed", ""
    if state.get("clarification"):
        return "clarified", ""
    # An error that still produced an answer is not an error outcome: something
    # failed on the way and the person got what they asked for anyway, which is
    # exactly what the no-node-raises rule is for. Only a run that ended with
    # nothing *and* an error is recorded as one.
    if state.get("summary") or state.get("observations"):
        return "answered", ""
    if state.get("error"):
        return "error", _short_error(state["error"])
    return "empty", ""


def _short_error(error: Any) -> str:
    """A groupable code from whatever was recorded in `state["error"]`.

    The state holds `str(exc)`, which is a message rather than a type — often
    with an id or a URL in it, which would make every row its own group. The
    leading token is close enough to a cause to count by.
    """
    text = str(error or "").strip()
    if not text:
        return ""
    return text.split(":")[0][:80]


async def record_attempt(
    session: AsyncSession,
    *,
    state: dict[str, Any],
    conversation_id: str | None,
    user_id: str | None,
    total_ms: int,
    settings: Settings | None = None,
    commit: bool = True,
) -> None:
    """Write one row describing how this run ended.

    Called from `AssistantAgentService` at both exits — the completed run and
    the one suspended at the gate — so a run that stops for a human is counted
    as having reached the gate rather than as not having happened.

    Failures are logged and swallowed; see the module docstring.
    """
    try:
        settings = settings or get_settings()
        provider, model = settings.resolve_assistant_llm()
        telemetry = state.get("telemetry") or {}
        observations = state.get("observations") or []

        outcome, error_code = classify(state)
        if outcome not in ASSISTANT_OUTCOMES:  # pragma: no cover - defensive
            outcome, error_code = "error", "unclassified"

        session.add(
            AssistantAttempt(
                conversation_id=conversation_id,
                user_id=user_id,
                source_message_id=telemetry.get("source_message_id") or None,
                outcome=outcome,
                provider=provider,
                model_configured=model,
                replans=int(state.get("replan_count") or 0),
                tool_calls=len(observations),
                tools_failed=sum(
                    1 for observation in observations if not observation.get("ok")
                ),
                tools_used=json.dumps(
                    [observation.get("tool", "") for observation in observations],
                    ensure_ascii=False,
                ),
                proposals_created=len(state.get("proposals") or []),
                proposals_executed=len(state.get("executed") or []),
                memory_lines=int(telemetry.get("memory_lines") or 0),
                memory_recalled=int(telemetry.get("memory_recalled") or 0),
                total_ms=int(total_ms),
                error_code=error_code,
            )
        )
        if commit:
            await session.commit()
        else:
            await session.flush()
    except Exception:
        logger.warning("Recording an assistant attempt failed", exc_info=True)
        if commit:
            # The failed insert poisoned the session; a caller that goes on to
            # use it would meet an error about this row rather than about its
            # own work.
            try:
                await session.rollback()
            except Exception:
                logger.warning("Rolling back after a failed attempt row failed")
