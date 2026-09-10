"""Aggregation helpers shared by the evaluation harness and the metrics report.

Two kinds of number live here. `percentile` and `group_scores` summarise a list
of scored samples and were written for `eval/run_eval.py`; they moved here so the
metrics report and the stats endpoint can use the same definitions rather than a
second implementation that drifts. `summarize_attempts` reads the
`translation_attempts` table and answers the questions the log exists for: what
share of translations fell back, which model actually served them, and how long
each language pair takes.

Nothing here writes. Nothing here raises on empty input — a report over a fresh
database prints zeroes rather than failing.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import AssistantAttempt, TranslationAttempt
from src.services.shared.llm_pricing import estimate_cost_usd

# Outcomes where the reader did not get the configured LLM's translation. Both
# are failures of the primary path, and both are invisible in
# `translation_results` alone, which is why the fallback rate is computed here.
FALLBACK_OUTCOMES = frozenset({"secondary", "original"})

def _normalize_language_code(value: str | None) -> str:
    """Return a stable base code so ``vi``, ``VI`` and ``vi-VN`` group alike."""
    normalized = (value or "unknown").strip().lower().replace("_", "-")
    return normalized.split("-", 1)[0]


def estimate_model_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimate cost using the same catalog exposed by the stats API."""
    return estimate_cost_usd(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def percentile(values: list[int], pct: float) -> float:
    """Percentile of a sequence, safe on empty and single-element input.

    ``statistics.quantiles`` needs at least two points; below that the only
    reasonable estimate is the single value itself.
    """
    if not values:
        return 0.0
    if len(values) < 2:
        return float(values[0])

    ordered = sorted(values)
    rank = pct / 100 * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def group_scores(
    scored: list[dict],
    key: str,
    pass_threshold: float,
) -> dict[str, tuple[int, float, int]]:
    """Group scored samples by one field: {value: (count, mean score, passed)}.

    Used for the per-dimension breakdowns (category, chat type, context level,
    language pair) so the report can show where the agent actually degrades
    rather than only a single aggregate number.

    The count is returned alongside the mean because several breakdowns have
    buckets of one or two samples, where a mean on its own invites more
    confidence than the sample size supports.
    """
    buckets: dict[str, list[float]] = {}
    for record in scored:
        buckets.setdefault(record.get(key) or "(unknown)", []).append(record["score"])

    return {
        value: (
            len(scores),
            statistics.mean(scores),
            sum(1 for score in scores if score >= pass_threshold),
        )
        for value, scores in sorted(buckets.items())
    }


@dataclass(frozen=True, slots=True)
class LanguagePairStats:
    """How many translations a language pair carried, and how long they took."""

    count: int
    p50_ms: float
    p95_ms: float
    avg_input_tokens: float
    avg_output_tokens: float


@dataclass(frozen=True, slots=True)
class ModelUsageStats:
    """One model's share of the traffic in `models_served`, with its tokens.

    Split from `models_served` (which stays `dict[str, int]` for
    `scripts/maintenance/report_metrics.py`, unchanged) rather than replacing it, so the
    admin stats screen can turn these into a cost estimate without every
    existing reader of `models_served` needing to learn a new shape.
    """

    count: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    """Everything the metrics report and the stats endpoint show.

    Counters are plain dicts so the whole object serialises to JSON without a
    custom encoder.
    """

    total: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    fallback_rate: float = 0.0
    detect_methods: dict[str, int] = field(default_factory=dict)
    fallback_reasons: dict[str, int] = field(default_factory=dict)
    models_served: dict[str, int] = field(default_factory=dict)
    model_usage: dict[str, ModelUsageStats] = field(default_factory=dict)
    language_pairs: dict[str, LanguagePairStats] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    priced_attempts: int = 0
    # The plain arithmetic mean, alongside the percentiles below. A mean is
    # what most readers of a latency figure expect "the average" to mean, and
    # it is easy to confuse with `total_ms_p50` — which is the *median*, the
    # middle attempt once every duration is sorted, not the mean of the fast
    # half. The two agree when durations are symmetric and diverge sharply
    # when a handful of slow outliers drag the mean up without moving the
    # median at all; showing both is what makes that visible instead of
    # picking one silently.
    total_ms_mean: float = 0.0
    total_ms_p50: float = 0.0
    total_ms_p95: float = 0.0


async def summarize_attempt_time_series(
    session: AsyncSession,
    *,
    days: int | None,
    now: datetime | None = None,
) -> list[dict[str, int | str]]:
    """Return real attempt telemetry grouped into chart-ready UTC buckets.

    The dashboard needs enough points to reveal a trend, not a raw log. The
    last 24 hours is grouped hourly, 7/30-day windows are grouped daily, and
    the all-time view is grouped monthly. Empty periods are returned as zeroes
    only after at least one attempt exists, so the client can distinguish an
    empty dataset from a quiet interval.
    """
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    all_time = days is None
    hourly = days == 1
    if all_time:
        start = None
        bucket_count = 0
        step = None
    elif hourly:
        start = (reference - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
        bucket_count = 24
        step = timedelta(hours=1)
    else:
        start = (reference - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        bucket_count = days
        step = timedelta(days=1)

    rows = (
        await session.execute(
            select(
                TranslationAttempt.created_at,
                TranslationAttempt.total_ms,
                TranslationAttempt.input_tokens,
                TranslationAttempt.output_tokens,
                TranslationAttempt.outcome,
            ).where(TranslationAttempt.created_at >= start)
            if start is not None
            else select(
                TranslationAttempt.created_at,
                TranslationAttempt.total_ms,
                TranslationAttempt.input_tokens,
                TranslationAttempt.output_tokens,
                TranslationAttempt.outcome,
            )
        )
    ).all()
    if not rows:
        return []

    grouped: dict[datetime, list[tuple[int, int, int, str]]] = defaultdict(list)
    for created_at, total_ms, input_tokens, output_tokens, outcome in rows:
        timestamp = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        if all_time:
            bucket = timestamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif hourly:
            bucket = timestamp.replace(minute=0, second=0, microsecond=0)
        else:
            bucket = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        grouped[bucket].append((int(total_ms or 0), int(input_tokens or 0), int(output_tokens or 0), str(outcome or "")))

    if all_time:
        start = min(grouped)
        end = max(grouped)
        buckets: list[datetime] = []
        bucket = start
        while bucket <= end:
            buckets.append(bucket)
            bucket = bucket.replace(year=bucket.year + 1, month=1) if bucket.month == 12 else bucket.replace(month=bucket.month + 1)
    else:
        assert start is not None and step is not None
        buckets = [start + step * index for index in range(bucket_count)]

    result: list[dict[str, int | str]] = []
    for bucket in buckets:
        entries = grouped.get(bucket, [])
        latencies = [entry[0] for entry in entries]
        result.append(
            {
                "time": bucket.strftime("%m/%Y") if all_time else bucket.strftime("%H:%M") if hourly else bucket.strftime("%d/%m"),
                "translations": len(entries),
                "input_tokens": sum(entry[1] for entry in entries),
                "output_tokens": sum(entry[2] for entry in entries),
                "p50_latency": round(percentile(latencies, 50)),
                "p95_latency": round(percentile(latencies, 95)),
                "fallback_count": sum(1 for entry in entries if entry[3] in FALLBACK_OUTCOMES),
            }
        )
    return result


async def summarize_attempts(
    session: AsyncSession,
    *,
    since: datetime | None = None,
) -> AttemptSummary:
    """Aggregate the attempt log into the figures the report displays.

    Rows are fetched and aggregated in Python rather than in SQL. Percentiles
    have no portable SQL form — SQLite has no `percentile_cont` — and computing
    part of the summary in the database and the rest here would mean two
    definitions of the same metric. The volume this project produces (one row
    per recipient bucket per message — a bucket being a language and a standing,
    so up to four per language) stays well inside what a single query can
    return; `since` is there for when it does not.

    `language_pairs` stays keyed by the language pair alone, deliberately.
    NFR-01 is about the delay a reader experiences, and that is the same
    question whichever standing they were translated at; splitting the key would
    turn one headline latency figure into four thinner samples. The consequence
    to keep in mind when reading it: `count` counts buckets, not messages.

    Every outcome — including the four no-translation exits `passthrough`,
    `timeout`, `error` and `empty` — counts in the operational `total`,
    `outcomes` and `fallback_rate`. That denominator is the entire reason
    `translation_attempts` records all four rather than only the three that
    reach `translation_results` (ADR-16).

    `language_pairs` and `models_served` are narrower than that. A row whose
    reading language already matched the message's own language is not a
    translation, whatever outcome it ended up recorded under — passthrough is
    the ordinary way there (routed before `build_context`, so no model and no
    fallback API ever ran: `src/agents/graph.py` `route_after_detect`,
    `src/agents/nodes/translation.py` `passthrough`), but a same-language
    bucket that timed out or errored before reaching that routing decision is
    exactly as uninformative — either way it would put a `"vi->vi"` row in
    `language_pairs`. Those rows, and their near-zero durations, are left out
    of `language_pairs` and `total_ms_p50`/`total_ms_p95` on that test, not on
    `outcome`. Timeout rows are also excluded from pair/model/latency success
    statistics: a timeout is an operational attempt, not a completed
    translation that should be labelled successful or failed for a language
    pair. `models_served` goes one step further: any row with no captured
    model name is left out of it, same-language or not, because a row nobody
    can name the model for has nothing to contribute to "which model served
    this" — that is what an uninformative `"(none)"` bucket was standing in
    for. It stays in `language_pairs` when it was a genuine attempt at a real
    pair, since the time spent is still real even when the model behind it is
    not known.

    Args:
        session: An open database session.
        since: Only count attempts created at or after this moment. None counts
            everything ever recorded.

    Returns:
        A summary with zeroes throughout when no attempt matches.
    """
    query = select(
        TranslationAttempt.outcome,
        TranslationAttempt.detect_method,
        TranslationAttempt.fallback_reason,
        TranslationAttempt.model_served,
        TranslationAttempt.source_language_declared,
        TranslationAttempt.source_language_detected,
        TranslationAttempt.target_language,
        TranslationAttempt.input_tokens,
        TranslationAttempt.output_tokens,
        TranslationAttempt.total_ms,
    )
    if since is not None:
        query = query.where(TranslationAttempt.created_at >= since)

    rows = (await session.execute(query)).all()
    if not rows:
        return AttemptSummary()

    outcomes: Counter[str] = Counter()
    detect_methods: Counter[str] = Counter()
    fallback_reasons: Counter[str] = Counter()
    models_served: Counter[str] = Counter()
    model_input_tokens: Counter[str] = Counter()
    model_output_tokens: Counter[str] = Counter()
    durations_by_pair: dict[str, list[int]] = {}
    input_tokens_by_pair: dict[str, list[int]] = {}
    output_tokens_by_pair: dict[str, list[int]] = {}
    all_durations: list[int] = []
    input_tokens = 0
    output_tokens = 0
    estimated_cost_usd = 0.0
    priced_attempts = 0

    for row in rows:
        outcomes[row.outcome] += 1
        detect_methods[row.detect_method or "(none)"] += 1
        if row.fallback_reason:
            fallback_reasons[row.fallback_reason] += 1
        input_tokens += row.input_tokens
        output_tokens += row.output_tokens
        attempt_cost = estimate_model_cost_usd(row.model_served, row.input_tokens, row.output_tokens)
        if attempt_cost is not None:
            estimated_cost_usd += attempt_cost
            priced_attempts += 1

        # The detected language is the one the translation was actually
        # performed from; the declared one is a guess from the sender's profile
        # and is wrong exactly when detection was worth running.
        source = row.source_language_detected or row.source_language_declared
        normalized_source = _normalize_language_code(source)
        normalized_target = _normalize_language_code(row.target_language)

        # Everything below this line describes translation work that actually
        # happened. A row whose reading language already matched its own is
        # not that, regardless of which outcome it was recorded under — see
        # the docstring above for why that check is on the languages and not
        # on `outcome == "passthrough"`.
        if normalized_source == normalized_target:
            continue

        # A timeout did not produce a translation result. Keep it in the
        # operational totals above, but do not turn it into a language-pair
        # success/failure row or let it distort completed-translation latency.
        if row.outcome == "timeout":
            continue

        # No captured model name means nothing here can say which model this
        # is evidence for, so it is not evidence for any of them — a `"(none)"`
        # bucket would only have restated that as a bucket.
        if row.model_served:
            models_served[row.model_served] += 1
            model_input_tokens[row.model_served] += row.input_tokens
            model_output_tokens[row.model_served] += row.output_tokens

        pair = f"{normalized_source}->{normalized_target}"
        durations_by_pair.setdefault(pair, []).append(row.total_ms)
        input_tokens_by_pair.setdefault(pair, []).append(row.input_tokens)
        output_tokens_by_pair.setdefault(pair, []).append(row.output_tokens)
        all_durations.append(row.total_ms)

    fallback_count = sum(outcomes[outcome] for outcome in FALLBACK_OUTCOMES)

    return AttemptSummary(
        total=len(rows),
        outcomes=dict(outcomes.most_common()),
        fallback_rate=fallback_count / len(rows),
        detect_methods=dict(detect_methods.most_common()),
        fallback_reasons=dict(fallback_reasons.most_common()),
        models_served=dict(models_served.most_common()),
        model_usage={
            model: ModelUsageStats(
                count=count,
                input_tokens=model_input_tokens[model],
                output_tokens=model_output_tokens[model],
            )
            for model, count in models_served.most_common()
        },
        language_pairs={
            pair: LanguagePairStats(
                count=len(durations),
                p50_ms=percentile(durations, 50),
                p95_ms=percentile(durations, 95),
                avg_input_tokens=sum(input_tokens_by_pair[pair]) / len(input_tokens_by_pair[pair]),
                avg_output_tokens=sum(output_tokens_by_pair[pair]) / len(output_tokens_by_pair[pair]),
            )
            for pair, durations in sorted(durations_by_pair.items())
        },
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        priced_attempts=priced_attempts,
        total_ms_mean=statistics.mean(all_durations) if all_durations else 0.0,
        total_ms_p50=percentile(all_durations, 50),
        total_ms_p95=percentile(all_durations, 95),
    )


# ---------------------------------------------------------------------------
# Assistant Agent (ADR-40)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssistantSummary:
    """What the assistant did, read back out of `assistant_attempts`.

    Separate from `AttemptSummary` rather than folded into it. The two agents
    are measured on different things — a fallback rate and a language pair mean
    nothing here, a confirmation rate and a replan distribution mean nothing
    there — and one object carrying both halves would be mostly zeroes whichever
    report read it.
    """

    total: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    # Runs that reached the gate, and how many of those were carried out inside
    # the same run. The denominator is every run, not every run that reached the
    # gate — which is the whole reason `refused`, `clarified` and `empty` rows
    # are written at all.
    proposals_created: int = 0
    proposals_executed: int = 0
    tool_calls: int = 0
    tools_failed: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    replans: dict[int, int] = field(default_factory=dict)
    # Share of recalled lines that came from `assistant_chunks` rather than the
    # recent window. Zero everywhere means the index is empty, which nothing
    # else in the system would report.
    memory_lines: int = 0
    memory_recalled: int = 0
    avg_ms: float = 0.0
    p95_ms: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)


async def summarize_assistant_attempts(
    session: AsyncSession,
    *,
    since: datetime | None = None,
) -> AssistantSummary:
    """Aggregate the assistant's attempt log.

    Reads only. Returns an empty summary rather than raising when the window
    holds nothing, so a report over a fresh database prints zeroes instead of a
    traceback.
    """
    statement = select(AssistantAttempt)
    if since is not None:
        statement = statement.where(AssistantAttempt.created_at >= since)
    rows = list((await session.scalars(statement)).all())
    if not rows:
        return AssistantSummary()

    outcomes: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    replans: dict[int, int] = {}
    errors: dict[str, int] = {}
    for row in rows:
        outcomes[row.outcome] = outcomes.get(row.outcome, 0) + 1
        replans[row.replans] = replans.get(row.replans, 0) + 1
        if row.error_code:
            errors[row.error_code] = errors.get(row.error_code, 0) + 1
        try:
            for name in json.loads(row.tools_used or "[]"):
                if isinstance(name, str) and name:
                    tool_counts[name] = tool_counts.get(name, 0) + 1
        except (TypeError, ValueError):
            # A malformed row costs one row's tool breakdown, never the report.
            continue

    durations = sorted(row.total_ms for row in rows)
    index = max(int(len(durations) * 0.95) - 1, 0)

    return AssistantSummary(
        total=len(rows),
        outcomes=outcomes,
        proposals_created=sum(row.proposals_created for row in rows),
        proposals_executed=sum(row.proposals_executed for row in rows),
        tool_calls=sum(row.tool_calls for row in rows),
        tools_failed=sum(row.tools_failed for row in rows),
        tool_counts=tool_counts,
        replans=replans,
        memory_lines=sum(row.memory_lines for row in rows),
        memory_recalled=sum(row.memory_recalled for row in rows),
        avg_ms=sum(durations) / len(durations),
        p95_ms=float(durations[index]),
        errors=errors,
    )
