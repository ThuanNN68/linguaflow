"""Owner-scoped ActionProposal persistence, temporal safety, and HITL transitions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ActionProposal, Message
from src.schemas.intelligence import ActionCandidateDTO

MAX_CLARIFICATION_ROUNDS = 2
_ACTIVE_STATUSES = ("needs_clarification", "pending_confirmation")
_RELATIVE_TIME = re.compile(
    r"\b(?:mai|ngày mai|tomorrow)\b(?:\s+(?:lúc|at))?\s*(?P<hour>\d{1,2})(?:[:h](?P<minute>\d{2})?)?",
    re.IGNORECASE,
)
_TOMORROW_MORNING = re.compile(r"\b(?:tomorrow morning|sáng mai)\b", re.IGNORECASE)
_NEXT_FRIDAY = re.compile(r"\bnext friday\b", re.IGNORECASE)
_LOCAL_DATE_TIME = re.compile(
    r"^\s*(?P<hour>\d{1,2})(?:\s*(?::|h|giờ)\s*(?P<minute>\d{1,2})?)?\s*"
    r"(?:ngày\s*)?(?P<day>\d{1,2})[/-](?P<month>\d{1,2})[/-](?P<year>\d{4})\s*$",
    re.IGNORECASE,
)
_RELATIVE_DAY = re.compile(r"\b(?P<day>hôm nay|today|ngày mai|mai|tomorrow|ngày kia)\b", re.IGNORECASE)
_CLOCK_TIME = re.compile(
    r"\b(?P<hour>\d{1,2})(?:\s*(?::|h|giờ)\s*(?P<minute>\d{1,2})?)?\b",
    re.IGNORECASE,
)
_DAY_PART = re.compile(
    r"\b(?P<part>sáng|trưa|chiều|tối|đêm|morning|afternoon|evening|night)\b",
    re.IGNORECASE,
)
_GENERIC_APPOINTMENT_WORDS = {
    "a",
    "an",
    "the",
    "cuộc",
    "lịch",
    "hẹn",
    "họp",
    "meeting",
    "appointment",
    "event",
}


class ActionProposalError(Exception):
    """Base proposal lifecycle error."""


class ActionProposalNotFoundError(ActionProposalError):
    """The proposal does not exist."""


class ActionProposalOwnershipError(ActionProposalError):
    """The authenticated user is not the proposal owner."""


class ActionProposalStatusError(ActionProposalError):
    """The requested transition is not valid for the current state."""


class ActionProposalAmbiguousTargetError(ActionProposalError):
    """A correction could refer to more than one undecided appointment."""


@dataclass(frozen=True)
class TemporalResolution:
    """Server-side temporal result; model datetimes are never authoritative."""

    scheduled_start_at: datetime | None
    raw_time_expression: str | None
    resolved_timezone: str | None
    missing_fields: tuple[str, ...]


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = _parse_explicit_offset(value)
        if parsed is None:
            raise ValueError("Datetime must be ISO 8601 with an explicit timezone")
        return parsed
    if value.tzinfo is None:
        raise ValueError("Datetime must include an explicit timezone")
    return value.astimezone(UTC)


def _valid_timezone(value: str | None) -> ZoneInfo | None:
    if not value:
        return None
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return None


def _parse_explicit_offset(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed) if parsed.tzinfo else None


def _relative_time_expression(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.casefold()
    return bool(
        _RELATIVE_DAY.search(lowered)
        or _RELATIVE_TIME.search(lowered)
        or _TOMORROW_MORNING.search(lowered)
        or _NEXT_FRIDAY.search(lowered)
    )


def _resolve_relative_time(raw: str, reference: datetime, timezone: ZoneInfo) -> datetime | None:
    """Resolve the deliberately small, execution-safe relative-time grammar."""

    local_reference = _as_utc(reference).astimezone(timezone)
    relative_day = _RELATIVE_DAY.search(raw)
    clock = _CLOCK_TIME.search(raw)
    if relative_day and clock:
        hour = int(clock.group("hour"))
        minute = int(clock.group("minute") or 0)
        part_match = _DAY_PART.search(raw)
        part = part_match.group("part").casefold() if part_match else None
        if part in {"chiều", "tối", "đêm", "afternoon", "evening", "night"} and 1 <= hour <= 11:
            hour += 12
        elif part in {"sáng", "morning"} and hour == 12:
            hour = 0
        elif part == "trưa" and 1 <= hour <= 3:
            hour += 12
        if hour > 23 or minute > 59:
            return None
        day_token = relative_day.group("day").casefold()
        day_offset = 0 if day_token in {"hôm nay", "today"} else 2 if day_token == "ngày kia" else 1
        local = datetime.combine(
            local_reference.date() + timedelta(days=day_offset),
            time(hour=hour, minute=minute),
            timezone,
        )
        return local.astimezone(UTC)

    match = _RELATIVE_TIME.search(raw)
    if match:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        if hour > 23 or minute > 59:
            return None
        local = datetime.combine(local_reference.date() + timedelta(days=1), time(hour=hour, minute=minute), timezone)
        return local.astimezone(UTC)
    if _TOMORROW_MORNING.search(raw):
        local = datetime.combine(local_reference.date() + timedelta(days=1), time(hour=9), timezone)
        return local.astimezone(UTC)
    if _NEXT_FRIDAY.search(raw):
        days = (4 - local_reference.weekday()) % 7 or 7
        local = datetime.combine(local_reference.date() + timedelta(days=days), time(hour=9), timezone)
        return local.astimezone(UTC)
    return None


def _resolve_local_date_time(answer: str | None, timezone: ZoneInfo) -> datetime | None:
    """Parse a user-supplied local date/time only when its timezone is trusted.

    Clarification answers arrive as ordinary Vietnamese text rather than ISO
    timestamps.  A full date plus time is unambiguous once the authenticated
    client supplies a valid IANA timezone, so it is safe to normalize here.
    """

    match = _LOCAL_DATE_TIME.match(answer or "")
    if match is None:
        return None
    try:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        local = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            hour,
            minute,
            tzinfo=timezone,
        )
    except ValueError:
        return None
    return local.astimezone(UTC)


def normalize_action_time(
    *,
    raw_time_expression: str | None,
    reference_timestamp: datetime | None,
    trusted_timezone: str | None,
    candidate_datetime: datetime | None = None,
    trusted_candidate_datetime: bool = False,
    clarification_answer: str | None = None,
    existing_missing_fields: list[str] | None = None,
) -> TemporalResolution:
    """Normalize temporal data without guessing a locale, timezone, or current time.

    An ISO value with an explicit offset is self-contained.  A relative value
    requires an explicit trusted IANA timezone and the source message timestamp;
    an LLM supplied UTC value cannot fill that gap.
    """

    raw = (raw_time_expression or "").strip() or None
    answer = (clarification_answer or "").strip() or None
    missing = set(existing_missing_fields or [])
    explicit = _parse_explicit_offset(answer) or _parse_explicit_offset(raw)
    if explicit is not None:
        missing.discard("time")
        missing.discard("timezone")
        return TemporalResolution(explicit, raw, None, tuple(sorted(missing)))

    # A timezone can be explicitly supplied in the trusted request field.  A
    # bare IANA timezone answer is also a direct answer to an existing timezone
    # question; it is validated with zoneinfo before being trusted.
    timezone_name = trusted_timezone
    if timezone_name is None and "timezone" in missing and answer and _valid_timezone(answer):
        timezone_name = answer
    timezone = _valid_timezone(timezone_name)

    if timezone is not None:
        local_answer = _resolve_local_date_time(answer, timezone)
        if local_answer is not None:
            missing.discard("time")
            missing.discard("timezone")
            return TemporalResolution(local_answer, raw, timezone.key, tuple(sorted(missing)))

    # A confirm payload is authenticated owner input, unlike model candidate
    # data. Its offset-bearing datetime is a complete representation of an
    # instant and may resolve a prior relative-time ambiguity without guessing
    # a locale or timezone.
    if candidate_datetime is not None and trusted_candidate_datetime:
        try:
            canonical_candidate = _as_utc(candidate_datetime)
        except ValueError:
            missing.add("time")
        else:
            missing.discard("time")
            missing.discard("timezone")
            return TemporalResolution(canonical_candidate, raw, None, tuple(sorted(missing)))

    if raw and _relative_time_expression(raw):
        if timezone is None or reference_timestamp is None:
            missing.update({"timezone", "time"})
            return TemporalResolution(None, raw, None, tuple(sorted(missing)))
        resolved = _resolve_relative_time(raw, reference_timestamp, timezone)
        if resolved is None:
            missing.add("time")
            return TemporalResolution(None, raw, timezone.key, tuple(sorted(missing)))
        missing.discard("time")
        missing.discard("timezone")
        return TemporalResolution(resolved, raw, timezone.key, tuple(sorted(missing)))

    # Candidate datetime is admissible only when it is not standing in for an
    # ambiguous relative expression.  This preserves explicit/manual candidate
    # data while refusing fabricated UTC for phrases such as "mai 9h".
    if candidate_datetime is not None:
        try:
            canonical_candidate = _as_utc(candidate_datetime)
            return TemporalResolution(canonical_candidate, raw, None, tuple(sorted(missing)))
        except ValueError:
            missing.add("time")
    return TemporalResolution(None, raw, None, tuple(sorted(missing)))


def compute_proposal_idempotency_key(
    owner_user_id: str,
    source_message_id: str,
    action_type: str,
    title: str,
    scheduled_time: datetime | str | None = None,
    source_mode: str = "on_demand",
    raw_time_expression: str | None = None,
) -> str:
    """Stable logical-action identity including the complete canonical time."""

    if isinstance(scheduled_time, datetime):
        stamp = _as_utc(scheduled_time).isoformat()
    elif scheduled_time:
        stamp = str(scheduled_time)
    else:
        # Two unresolved relative expressions are not the same logical action.
        # Collapse harmless whitespace/casing differences, never weekday/hour.
        stamp = "raw:" + " ".join((raw_time_expression or "").casefold().split())
    value = ":".join(
        (
            owner_user_id,
            source_message_id,
            source_mode,
            action_type.casefold(),
            title.strip().casefold(),
            stamp,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reminder_lead(minutes: int | None) -> timedelta | None:
    """Turn the approver's answer into what `create_event` expects.

    ``None`` means no reminder, matching `CalendarEventCreateRequest` exactly:
    the default lives in the schema, so by the time a value reaches here the
    only remaining question is whether the person asked not to be nudged.
    """
    return None if minutes is None else timedelta(minutes=minutes)


def _valid_candidate_end(
    candidate_end: datetime | None,
    scheduled_start: datetime | None,
) -> datetime | None:
    """Keep an extracted end only when it is a valid same-event interval.

    The model may report a malformed interval, but it must never turn that
    into a proposal whose end precedes its start.  A missing/invalid end is
    deliberately left for the approval UI's duration default instead.
    """
    if candidate_end is None or scheduled_start is None:
        return None
    try:
        canonical_end = _as_utc(candidate_end)
    except ValueError:
        return None
    return canonical_end if canonical_end > scheduled_start else None


# The extractor and the time normalizer grew separate names for the same gap.
# `ActionCandidateDTO` reports a missing start as `scheduled_time`; everything
# that resolves one -- `normalize_action_time`, and the clarification and
# confirmation paths that read its output -- speaks of `time`. Nothing ever
# translated between them, so a proposal the extractor marked `scheduled_time`
# could never be completed: clarification did not recognise it as temporal and
# so never cleared it, and confirmation then refused with "still has unresolved
# required fields". Answering the question and pressing approve returned an
# error every time, with no way forward.
#
# Both names are normalised on the way in, here, so existing rows are fixed as
# they are read rather than needing a data migration.
_MISSING_FIELD_ALIASES = {"scheduled_time": "time", "scheduled_start_at": "time"}


def _load_missing(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ["manual_correction_required"]
    return [_MISSING_FIELD_ALIASES.get(item, item) for item in parsed if isinstance(item, str)]


def _dump_missing(values: list[str] | tuple[str, ...] | set[str]) -> str:
    return json.dumps(sorted(set(values)))


def _appointment_title_tokens(value: str) -> set[str]:
    """Extract meaningful words used to identify an amendment target."""
    return {
        token
        for token in re.findall(r"\w+", value.casefold(), flags=re.UNICODE)
        if len(token) > 1 and token not in _GENERIC_APPOINTMENT_WORDS
    }


class ActionProposalService:
    """Transactional proposal service.  The caller owns the outer session."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_proposal(self, proposal_id: str) -> ActionProposal:
        proposal = await self.db.scalar(select(ActionProposal).where(ActionProposal.id == proposal_id))
        if proposal is None:
            raise ActionProposalNotFoundError("Action proposal not found")
        return proposal

    async def _appointment_amendment_target(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        candidate_title: str,
    ) -> ActionProposal | None:
        active = list(
            (
                await self.db.scalars(
                    select(ActionProposal)
                    .where(
                        ActionProposal.conversation_id == conversation_id,
                        ActionProposal.owner_user_id == owner_user_id,
                        ActionProposal.action_type == "appointment",
                        ActionProposal.status.in_(_ACTIVE_STATUSES),
                    )
                    .order_by(ActionProposal.updated_at.desc(), ActionProposal.id.desc())
                    .limit(10)
                    .with_for_update()
                )
            ).all()
        )
        if not active:
            return None
        if len(active) == 1:
            return active[0]

        requested = _appointment_title_tokens(candidate_title)
        scored = [
            (len(requested.intersection(_appointment_title_tokens(proposal.title))), proposal) for proposal in active
        ]
        best_score = max(score for score, _ in scored)
        best = [proposal for score, proposal in scored if score == best_score and score > 0]
        if len(best) == 1:
            return best[0]
        raise ActionProposalAmbiguousTargetError("More than one pending appointment matches this correction")

    async def _get_owned(self, proposal_id: str, user_id: str) -> ActionProposal:
        proposal = await self.get_proposal(proposal_id)
        if proposal.owner_user_id != user_id:
            raise ActionProposalOwnershipError("Only the assigned owner may access this proposal")
        return proposal

    async def list_for_owner(
        self,
        user_id: str,
        status_filter: str | None = None,
        conversation_id: str | None = None,
    ) -> list[ActionProposal]:
        """The owner's proposals, most recent first, minus the ones they cleared."""
        statement = select(ActionProposal).where(
            ActionProposal.owner_user_id == user_id,
            ActionProposal.dismissed_at.is_(None),
        )
        if status_filter:
            statement = statement.where(ActionProposal.status == status_filter)
        if conversation_id:
            statement = statement.where(ActionProposal.conversation_id == conversation_id)
        return list((await self.db.scalars(statement.order_by(ActionProposal.created_at.desc()))).all())

    async def dismiss(self, *, proposal_id: str, user_id: str) -> ActionProposal:
        """Clear one proposal out of its owner's task inbox.

        Hiding, not deleting. An approved proposal has already produced a
        calendar event, and that event keeps its reminders and still fires when
        it comes due -- somebody tidying a list of finished items is not asking
        to cancel their meetings. Removing the row would take the calendar entry
        with it through `calendar_events.action_proposal_id`.

        Idempotent: dismissing an already-dismissed proposal keeps the first
        timestamp, so a double click does not rewrite history.
        """
        proposal = await self.db.get(ActionProposal, proposal_id)
        if proposal is None:
            raise ActionProposalNotFoundError(proposal_id)
        if proposal.owner_user_id != user_id:
            raise ActionProposalOwnershipError(proposal_id)
        if proposal.dismissed_at is None:
            proposal.dismissed_at = datetime.now(UTC)
            await self.db.commit()
            await self.db.refresh(proposal)
        return proposal

    async def dismiss_all_decided(self, *, user_id: str) -> int:
        """Clear every proposal the owner has already decided on.

        Only decided ones. Sweeping away something still awaiting a decision
        would silently drop a question the assistant is waiting on, and the
        person would never learn it had been asked.
        """
        result = await self.db.execute(
            update(ActionProposal)
            .where(
                ActionProposal.owner_user_id == user_id,
                ActionProposal.dismissed_at.is_(None),
                ActionProposal.status.in_(("confirmed", "rejected", "stale")),
            )
            .values(dismissed_at=datetime.now(UTC))
        )
        await self.db.commit()
        return int(result.rowcount or 0)

    async def create_proposals_from_candidates(
        self,
        conversation_id: str,
        source_message_id: str,
        candidates: list[ActionCandidateDTO],
        *,
        owner_user_id: str,
        source_mode: str,
        created_by_user_id: str | None = None,
        trusted_timezone: str | None = None,
        field_source_message_ids: dict[str, str] | None = None,
        update_latest_appointment: bool = False,
    ) -> list[ActionProposal]:
        """Persist candidates with one savepoint per insert conflict.

        A duplicate cannot roll back an earlier successful candidate in the same
        outer transaction.  Candidate-provided owners are deliberately ignored.
        """

        source = await self.db.get(Message, source_message_id)
        if source is None or source.conversation_id != conversation_id:
            raise ActionProposalNotFoundError("Source message not found")

        # Serialize proposal writes for one owner/conversation across processes.
        # Row locks cannot help when both workers observe that no row exists yet;
        # this transaction-scoped advisory lock closes that insertion race and
        # is released automatically on commit or rollback.
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"action-proposal:{conversation_id}:{owner_user_id}"},
        )

        saved: list[ActionProposal] = []
        sources = field_source_message_ids or {}
        for candidate in candidates:
            normalized = normalize_action_time(
                raw_time_expression=candidate.raw_time_expression,
                reference_timestamp=source.created_at,
                trusted_timezone=trusted_timezone,
                candidate_datetime=candidate.scheduled_time,
                existing_missing_fields=candidate.missing_fields,
            )
            key = compute_proposal_idempotency_key(
                owner_user_id,
                source_message_id,
                candidate.action_type,
                candidate.title,
                normalized.scheduled_start_at,
                source_mode,
                normalized.raw_time_expression,
            )
            existing = await self.db.scalar(select(ActionProposal).where(ActionProposal.idempotency_key == key))
            if existing is not None:
                saved.append(existing)
                continue

            missing = list(normalized.missing_fields)
            scheduled_end_at = _valid_candidate_end(
                candidate.scheduled_end_time,
                normalized.scheduled_start_at,
            )
            if update_latest_appointment and candidate.action_type == "appointment":
                existing_active = await self._appointment_amendment_target(
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    candidate_title=candidate.title,
                )
                if existing_active is not None:
                    # A correction belongs to the newest undecided appointment,
                    # never to a confirmed calendar entry. Newer explicit fields
                    # replace older ones, which resolves "9h" then "đổi 10h"
                    # without creating a second card.
                    previous_source = await self.db.get(Message, existing_active.source_message_id)
                    if previous_source is not None and (
                        previous_source.created_at,
                        previous_source.id,
                    ) > (source.created_at, source.id):
                        # Completion order is not message order. An older LLM
                        # run finishing last may not undo a newer correction.
                        saved.append(existing_active)
                        continue
                    existing_active.source_message_id = source_message_id
                    # Corrections such as "move it to 10 PM" often make the
                    # extractor return a generic title ("Meeting"). Keep the
                    # established title unless the old row genuinely lacks one.
                    if not existing_active.title and candidate.title:
                        existing_active.title = candidate.title
                    existing_active.details = candidate.details or existing_active.details
                    existing_active.location = getattr(candidate, "location", None) or existing_active.location
                    existing_active.raw_time_expression = (
                        normalized.raw_time_expression or existing_active.raw_time_expression
                    )
                    if normalized.scheduled_start_at is not None:
                        existing_active.scheduled_start_at = normalized.scheduled_start_at
                        existing_active.scheduled_time = normalized.scheduled_start_at
                        existing_active.due_at = normalized.scheduled_start_at
                        existing_active.time_source_message_id = sources.get("time", source_message_id)
                    if scheduled_end_at is not None:
                        existing_active.scheduled_end_at = scheduled_end_at
                    if candidate.details:
                        existing_active.details_source_message_id = sources.get("details", source_message_id)
                    if normalized.resolved_timezone is not None:
                        existing_active.resolved_timezone = normalized.resolved_timezone
                    # The amendment can mention only one field. Do not turn a
                    # previously complete note/location back into a missing
                    # value merely because that field was absent from the new
                    # short message.
                    missing = set(_load_missing(existing_active.missing_fields)) | set(missing)
                    if existing_active.scheduled_start_at is not None:
                        missing.discard("time")
                    if existing_active.title:
                        missing.discard("title")
                    if existing_active.details:
                        missing.discard("details")
                    if existing_active.location:
                        missing.discard("location")
                    if existing_active.resolved_timezone:
                        missing.discard("timezone")
                    existing_active.missing_fields = _dump_missing(missing)
                    existing_active.status = "needs_clarification" if missing else "pending_confirmation"
                    existing_active.updated_at = datetime.now(UTC)
                    saved.append(existing_active)
                    continue
            if candidate.action_type == "appointment" and not update_latest_appointment:
                # The original request and a rapid follow-up may each have a
                # worker. Reuse the newer card when it is also the message that
                # supplied one of the fields this older worker just extracted.
                source_ids = set(sources.values())
                active = (
                    await self.db.scalar(
                        select(ActionProposal)
                        .where(
                            ActionProposal.conversation_id == conversation_id,
                            ActionProposal.owner_user_id == owner_user_id,
                            ActionProposal.action_type == "appointment",
                            ActionProposal.status.in_(_ACTIVE_STATUSES),
                            ActionProposal.source_message_id.in_(source_ids),
                        )
                        .order_by(ActionProposal.updated_at.desc(), ActionProposal.id.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    if source_ids
                    else None
                )
                if active is not None and active.source_message_id in set(sources.values()):
                    active_source = await self.db.get(Message, active.source_message_id)
                    if active_source is not None and (
                        active_source.created_at,
                        active_source.id,
                    ) > (source.created_at, source.id):
                        saved.append(active)
                        continue
            proposal = ActionProposal(
                conversation_id=conversation_id,
                source_message_id=source_message_id,
                time_source_message_id=sources.get("time", source_message_id)
                if normalized.scheduled_start_at is not None
                else None,
                details_source_message_id=sources.get("details", source_message_id) if candidate.details else None,
                owner_user_id=owner_user_id,
                created_by_user_id=created_by_user_id,
                source_mode=source_mode,
                action_type=candidate.action_type,
                status="needs_clarification" if missing else "pending_confirmation",
                title=candidate.title,
                details=candidate.details,
                location=getattr(candidate, "location", None),
                raw_time_expression=normalized.raw_time_expression,
                scheduled_start_at=normalized.scheduled_start_at,
                scheduled_end_at=scheduled_end_at,
                # Retained for legacy clients; it is always mirrored from the
                # canonical start time and never independently normalized.
                scheduled_time=normalized.scheduled_start_at,
                resolved_timezone=normalized.resolved_timezone,
                confidence_score=candidate.confidence_score,
                clarification_prompt=candidate.clarification_prompt,
                clarification_question=candidate.clarification_prompt,
                missing_fields=_dump_missing(missing),
                idempotency_key=key,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(proposal)
                    await self.db.flush()
                saved.append(proposal)
            except IntegrityError:
                existing = await self.db.scalar(select(ActionProposal).where(ActionProposal.idempotency_key == key))
                if existing is None:
                    raise
                saved.append(existing)

        await self.db.commit()
        return saved

    async def confirm_proposal(
        self,
        proposal_id: str,
        user_id: str,
        corrections: dict[str, Any] | None = None,
        reminder_minutes_before: int | None = 15,
    ) -> ActionProposal:
        """Approve one proposal, optionally correcting it on the way through.

        Args:
            corrections: Fields the approver changed. Only the ones in
                ``allowed`` below are honoured — the rest of the row is
                provenance and must not be editable from a confirmation.
            reminder_minutes_before: How far ahead to nudge, chosen at approval
                because the message the proposal came from never says it.
                ``None`` means no reminder — a real choice, not a missing value.
                A separate argument rather than another correction: it shapes
                the *calendar entry*, not the proposal, and putting it in
                ``allowed`` would try to write it to a column that does not
                exist.
        """
        proposal = await self._get_owned(proposal_id, user_id)
        if proposal.status not in _ACTIVE_STATUSES:
            raise ActionProposalStatusError("Proposal is not confirmable")

        allowed = {
            "title",
            "details",
            "location",
            "scheduled_start_at",
            "scheduled_end_at",
            "due_at",
            "resolved_timezone",
            "timezone",
        }
        values = {key: value for key, value in (corrections or {}).items() if key in allowed}
        if "timezone" in values:
            values["resolved_timezone"] = values.pop("timezone")
        for temporal_field in ("scheduled_start_at", "scheduled_end_at", "due_at"):
            if temporal_field in values:
                values[temporal_field] = _as_utc(values[temporal_field])
        if "scheduled_start_at" in values:
            values["scheduled_time"] = values["scheduled_start_at"]

        missing = _load_missing(proposal.missing_fields)
        if proposal.status == "needs_clarification":
            source = await self.db.get(Message, proposal.source_message_id)
            if source is None:
                raise ActionProposalStatusError("Source message is unavailable")
            timezone = values.get("resolved_timezone")
            resolution = normalize_action_time(
                raw_time_expression=proposal.raw_time_expression,
                reference_timestamp=source.created_at,
                trusted_timezone=timezone,
                candidate_datetime=values.get("scheduled_start_at"),
                trusted_candidate_datetime=True,
                existing_missing_fields=missing,
            )
            remaining = set(resolution.missing_fields)
            for field in ("title", "details", "location"):
                if field in remaining and values.get(field):
                    remaining.remove(field)
            missing = list(remaining)
            if resolution.scheduled_start_at is not None:
                values["scheduled_start_at"] = resolution.scheduled_start_at
                values["scheduled_time"] = resolution.scheduled_start_at
            if resolution.resolved_timezone is not None:
                values["resolved_timezone"] = resolution.resolved_timezone
            if missing:
                raise ActionProposalStatusError("Proposal still has unresolved required fields")
            values["missing_fields"] = "[]"
        elif missing:
            raise ActionProposalStatusError("Proposal still has unresolved required fields")
        now = datetime.now(UTC)
        values.update(
            status="confirmed",
            confirmed_by_user_id=user_id,
            confirmed_at=now,
            updated_at=now,
        )
        result = await self.db.execute(
            update(ActionProposal)
            .where(
                ActionProposal.id == proposal_id,
                ActionProposal.owner_user_id == user_id,
                ActionProposal.status == proposal.status,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            await self.db.rollback()
            raise ActionProposalStatusError("Proposal was already transitioned")

        # Before the commit, not after. The conditional UPDATE above is what
        # makes exactly one caller the winner of a concurrent confirm; putting
        # the calendar write inside the same transaction means the winner is
        # also the only one that schedules anything, and that a confirmation
        # cannot succeed while leaving the calendar empty.
        #
        # Imported here rather than at module scope: `calendar` imports
        # `ActionProposal` from the models module this one also uses, and a
        # top-level import would close the cycle.
        from src.services.calendar.calendar import CalendarService

        confirmed = await self.db.get(ActionProposal, proposal_id)
        if confirmed is not None:
            await self.db.refresh(confirmed)
            await CalendarService(self.db).schedule_from_proposal(
                confirmed,
                commit=False,
                reminder_lead=_reminder_lead(reminder_minutes_before),
            )

        await self.db.commit()
        return await self.get_proposal(proposal_id)

    async def reject_proposal(self, proposal_id: str, user_id: str) -> ActionProposal:
        proposal = await self._get_owned(proposal_id, user_id)
        if proposal.status not in _ACTIVE_STATUSES:
            raise ActionProposalStatusError("Proposal is not rejectable")
        result = await self.db.execute(
            update(ActionProposal)
            .where(
                ActionProposal.id == proposal_id,
                ActionProposal.owner_user_id == user_id,
                ActionProposal.status.in_(_ACTIVE_STATUSES),
            )
            .values(status="rejected", rejected_at=datetime.now(UTC))
        )
        if result.rowcount != 1:
            await self.db.rollback()
            raise ActionProposalStatusError("Proposal was already transitioned")
        await self.db.commit()
        return await self.get_proposal(proposal_id)

    async def delete_terminal_proposal(self, proposal_id: str, user_id: str) -> None:
        """Remove an owner-visible rejected or stale proposal from the inbox."""

        proposal = await self._get_owned(proposal_id, user_id)
        if proposal.status not in ("rejected", "stale"):
            raise ActionProposalStatusError("Only rejected or stale proposals can be deleted")
        await self.db.delete(proposal)
        await self.db.commit()

    async def clarify(
        self,
        proposal_id: str,
        user_id: str,
        answer: str,
        timezone: str | None,
    ) -> ActionProposal:
        """Resolve only missing fields; clarification can never confirm a proposal."""

        proposal = await self._get_owned(proposal_id, user_id)
        if proposal.status != "needs_clarification":
            raise ActionProposalStatusError("Proposal does not need clarification")
        if proposal.clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
            raise ActionProposalStatusError("manual_correction_required")
        source = await self.db.get(Message, proposal.source_message_id)
        if source is None:
            raise ActionProposalStatusError("Source message is unavailable")

        missing = _load_missing(proposal.missing_fields)
        temporal_missing = {"time", "timezone"}.intersection(missing)
        if temporal_missing or _relative_time_expression(proposal.raw_time_expression):
            resolution = normalize_action_time(
                raw_time_expression=proposal.raw_time_expression,
                reference_timestamp=source.created_at,
                trusted_timezone=timezone,
                clarification_answer=answer,
                candidate_datetime=None,
                existing_missing_fields=missing,
            )
            remaining = set(resolution.missing_fields)
        else:
            resolution = TemporalResolution(None, proposal.raw_time_expression, None, tuple(missing))
            remaining = set(missing)

        # Deterministic structured clarification: an answer can fill only a
        # field that the persisted proposal explicitly marked as missing.
        non_temporal = [field for field in ("location", "title", "details") if field in remaining]
        resolved_non_temporal: dict[str, str] = {}
        if len(non_temporal) == 1 and answer.strip():
            field = non_temporal[0]
            resolved_non_temporal[field] = answer.strip()
            remaining.remove(field)
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "clarification_rounds": proposal.clarification_rounds + 1,
            "missing_fields": _dump_missing(remaining),
            "clarification_prompt": (None if not remaining else "Please provide the remaining execution details."),
            "updated_at": now,
        }
        values["clarification_question"] = values["clarification_prompt"]
        values.update(resolved_non_temporal)
        if resolution.scheduled_start_at is not None:
            values["scheduled_start_at"] = resolution.scheduled_start_at
            values["scheduled_time"] = resolution.scheduled_start_at
        if resolution.resolved_timezone is not None:
            values["resolved_timezone"] = resolution.resolved_timezone
        values["status"] = "pending_confirmation" if not remaining else "needs_clarification"
        result = await self.db.execute(
            update(ActionProposal)
            .where(
                ActionProposal.id == proposal_id,
                ActionProposal.owner_user_id == user_id,
                ActionProposal.status == "needs_clarification",
                ActionProposal.clarification_rounds == proposal.clarification_rounds,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            await self.db.rollback()
            raise ActionProposalStatusError("Proposal was already transitioned")
        await self.db.commit()
        return await self.get_proposal(proposal_id)

    async def mark_proposals_stale_for_message(self, message_id: str, *, commit: bool = True) -> int:
        result = await self.db.execute(
            update(ActionProposal)
            .where(
                or_(
                    ActionProposal.source_message_id == message_id,
                    ActionProposal.time_source_message_id == message_id,
                    ActionProposal.details_source_message_id == message_id,
                ),
                ActionProposal.status.in_(_ACTIVE_STATUSES),
            )
            .values(status="stale", stale_at=datetime.now(UTC))
        )
        if commit:
            await self.db.commit()
        return result.rowcount or 0
