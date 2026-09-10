"""Propose glossary entries from corrections several people made independently.

Runs offline, on demand or from cron. Nothing here is on the path of a message:
the clustering is cheap but the model call is not, and neither belongs in a
chat request.

    python scripts/maintenance/mine_glossary.py --since 7d --no-write
    make glossary-mine

The pipeline is five steps, and four of them are about refusing things:

1. read `correction_log` rows in the window that carry consent;
2. group them by meaning, not by text, so "staging env" and "môi trường stg"
   count as one disagreement rather than two;
3. drop any group too small, or made by too few different people, to be
   anything but one person's preference;
4. drop any group that repeats a term already active, or one an administrator
   has already refused — asking again about a differently-worded version of a
   rejected term is how a review queue stops being read;
5. ask a model to state what is left as a dictionary entry, and let it answer
   "this is not a term" for the many corrections that are a rephrasing.

Costs quota: one model call per surviving cluster. `--no-write` prints what it
would create and writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from src.agents.prompts import PROPOSE_GLOSSARY_TERM_PROMPT  # noqa: E402
from src.config import configure_logging, get_settings  # noqa: E402
from src.database import get_async_session_maker  # noqa: E402
from src.database.models import (  # noqa: E402
    GLOSSARY_AUDIENCES,
    GLOSSARY_DOMAINS,
    CorrectionLog,
    GlossaryEntry,
    GlossaryProposal,
    GlossaryProposalCitation,
)
from src.services.language.glossary import normalize_scope, normalize_term  # noqa: E402
from src.services.language.glossary_mining import (  # noqa: E402
    Cluster,
    cluster_corrections,
    is_already_known,
)
from src.services.shared.embeddings import embed_with_model  # noqa: E402
from src.services.shared.llm import extract_text, get_llm  # noqa: E402

logger = logging.getLogger(__name__)

_WINDOW = re.compile(r"^(\d+)([hdw])$")
_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def parse_window(value: str) -> timedelta:
    """Read `7d`, `12h` or `2w` into a duration."""
    match = _WINDOW.match(value.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            f"Expected a window like 7d, 12h or 2w, got {value!r}"
        )
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


async def load_corrections(
    session: AsyncSession, *, since: datetime, limit: int
) -> list[CorrectionLog]:
    """Read the consented corrections in the window.

    The consent filter is in the query rather than in a later loop. It is the
    condition that makes this whole script legitimate under ADR-19, and a
    condition like that belongs where it cannot be skipped by a code path added
    afterwards.
    """
    return list(
        (
            await session.scalars(
                select(CorrectionLog)
                .where(
                    CorrectionLog.consent_to_share.is_(True),
                    CorrectionLog.observed_at >= since,
                )
                .order_by(CorrectionLog.observed_at)
                .limit(limit)
            )
        ).all()
    )


async def load_known_terms(
    session: AsyncSession, *, source_language: str, target_language: str
) -> list[tuple[str, Any]]:
    """Everything already decided for one language pair.

    Active entries and rejected proposals answer to the same check for opposite
    reasons: one is already in the glossary and needs no proposal, the other was
    turned down and must not come back wearing different words.
    """
    entries = (
        await session.execute(
            select(GlossaryEntry.target_term, GlossaryEntry.embedding).where(
                GlossaryEntry.source_language == source_language,
                GlossaryEntry.target_language == target_language,
                GlossaryEntry.status == "active",
            )
        )
    ).all()
    refused = (
        await session.execute(
            select(GlossaryProposal.target_term, GlossaryProposal.embedding).where(
                GlossaryProposal.source_language == source_language,
                GlossaryProposal.target_language == target_language,
                GlossaryProposal.status.in_(("rejected", "pending")),
            )
        )
    ).all()
    return [
        (normalize_term(term), embedding) for term, embedding in [*entries, *refused]
    ]


async def describe(cluster: Cluster, llm: Any) -> dict[str, Any] | None:
    """Ask the model to state a cluster as a dictionary entry.

    Returns None when the model says this is not a term, or when the answer does
    not parse. Both are ordinary: most corrections are a rephrasing, and a
    proposal nobody can act on is worse than no proposal.
    """
    machine_phrase, human_phrase = cluster.modal_pair()
    source_language, target_language, _, _ = cluster.scope
    prompt = PROPOSE_GLOSSARY_TERM_PROMPT.format(
        source_language=source_language,
        target_language=target_language,
        domains=", ".join(f"`{value}`" for value in GLOSSARY_DOMAINS),
        audiences=", ".join(f"`{value}`" for value in GLOSSARY_AUDIENCES),
        machine_phrase=machine_phrase,
        human_phrase=human_phrase,
        occurrence_count=cluster.occurrence_count,
        distinct_user_count=cluster.distinct_user_count,
        citations="\n".join(f"- {line}" for line in cluster.citations()) or "- (none)",
    )
    try:
        answer = extract_text(await llm.ainvoke([{"role": "user", "content": prompt}]))
    except Exception as exc:
        logger.warning("Describing a cluster failed: %s", exc)
        return None

    text = answer.strip()
    if text.startswith("```"):
        text = text.strip("`")
        _, _, text = text.partition("\n")
    try:
        payload = json.loads(text.strip())
    except ValueError:
        logger.warning("Model answered with %d characters of non-JSON", len(text))
        return None

    if not isinstance(payload, dict) or payload.get("skip"):
        return None
    if not payload.get("source_term") or not payload.get("target_term"):
        return None
    return payload


async def mine(args: argparse.Namespace) -> int:
    """Run the pipeline. Returns the number of proposals created."""
    settings = get_settings()
    session_factory = get_async_session_maker()
    since = datetime.now(UTC) - args.since

    async with session_factory() as session:
        corrections = await load_corrections(session, since=since, limit=args.limit)

    print(f"Read {len(corrections)} consented corrections since {since:%Y-%m-%d %H:%M}")
    if not corrections:
        return 0

    clusters = cluster_corrections(
        corrections,
        similarity=args.similarity,
        min_count=args.min_count,
        min_users=args.min_users,
    )
    print(
        f"{len(clusters)} clusters reached {args.min_count} corrections "
        f"from {args.min_users} different people"
    )
    if not clusters:
        return 0

    llm = get_llm()
    created = 0

    for cluster in clusters:
        source_language, target_language, domain, audience = cluster.scope
        async with session_factory() as session:
            known = await load_known_terms(
                session,
                source_language=source_language,
                target_language=target_language,
            )
        if is_already_known(cluster, known, similarity=args.similarity):
            machine_phrase, human_phrase = cluster.modal_pair()
            print(f"  skip (already decided): {machine_phrase!r} -> {human_phrase!r}")
            continue

        described = await describe(cluster, llm)
        if described is None:
            machine_phrase, human_phrase = cluster.modal_pair()
            print(f"  skip (not a term): {machine_phrase!r} -> {human_phrase!r}")
            continue

        source_term = str(described["source_term"])[:200]
        target_term = str(described["target_term"])[:200]
        print(
            f"  propose: {source_term!r} -> {target_term!r} "
            f"({cluster.occurrence_count} corrections, "
            f"{cluster.distinct_user_count} people)"
        )
        if args.no_write:
            continue

        vector, vector_model = await embed_with_model(source_term, settings=settings)
        async with session_factory() as session:
            proposal = GlossaryProposal(
                source_term=source_term,
                source_term_normalized=normalize_term(source_term),
                target_term=target_term,
                source_language=source_language,
                target_language=target_language,
                # The scope the lookup will compare by equality, so a word
                # outside the closed vocabulary is dropped rather than stored
                # as a scope no conversation is ever profiled under.
                domain=normalize_scope(described.get("domain"), GLOSSARY_DOMAINS) or domain,
                audience=normalize_scope(described.get("audience"), GLOSSARY_AUDIENCES)
                or audience,
                keep_verbatim=bool(described.get("keep_verbatim")),
                status="pending",
                occurrence_count=cluster.occurrence_count,
                distinct_user_count=cluster.distinct_user_count,
                rationale=str(described.get("rationale") or "")[:1000],
                embedding=vector,
                embedding_model=vector_model,
            )
            session.add(proposal)
            await session.flush()
            for snippet in cluster.citations():
                session.add(
                    GlossaryProposalCitation(
                        proposal_id=proposal.id, anonymized_snippet=snippet
                    )
                )
            await session.commit()
        created += 1

    return created


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(
        description="Propose glossary entries from consented corrections."
    )
    parser.add_argument(
        "--since",
        type=parse_window,
        default=parse_window("7d"),
        help="How far back to read corrections: 7d, 12h, 2w. Default 7d.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Maximum corrections to read. Default 2000.",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=3,
        help=(
            "Corrections a cluster needs before it is proposed. Default 3. "
            "Lowering this turns one-off disagreements into house style."
        ),
    )
    parser.add_argument(
        "--min-users",
        type=int,
        default=2,
        help=(
            "Different people a cluster needs. Default 2. This is what "
            "separates a house style from one person's preference."
        ),
    )
    parser.add_argument(
        "--similarity",
        type=float,
        default=0.85,
        help="Cosine similarity for grouping corrections. Default 0.85.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print what would be proposed and write nothing.",
    )
    args = parser.parse_args()

    configure_logging(get_settings())
    created = asyncio.run(mine(args))
    if args.no_write:
        print("\nNothing written (--no-write).")
    else:
        print(f"\n{created} proposals created, waiting for review.")


if __name__ == "__main__":
    main()
