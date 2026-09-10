"""Load the starter glossary so a fresh database can demonstrate the feature.

The mining pipeline needs several people to correct the same term before it
proposes anything, which is right for discovering a house style and useless for
a demonstration and for the first weeks of real use. This loads a curated set
instead.

    python scripts/development/seed_glossary.py
    python scripts/development/seed_glossary.py --file seed/glossary_en_vi.jsonl --no-embed
    make seed-glossary

Safe to run repeatedly. Entries are matched on the same key the database is
unique on — normalised term, language pair, domain, audience — and an existing
one is updated rather than duplicated, so editing the file and re-running is the
intended way to change the glossary during development.

Embedding is on by default and costs one call per entry. `--no-embed` skips it:
the entries still work by exact match, they simply will not be found by the
variants people actually type until something backfills the vectors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from src.config import configure_logging, get_settings  # noqa: E402
from src.database import get_async_session_maker  # noqa: E402
from src.database.models import GlossaryEntry  # noqa: E402
from src.services.language.glossary import normalize_term  # noqa: E402
from src.services.shared.embeddings import embed, embedding_model_name  # noqa: E402

DEFAULT_FILE = Path("seed/glossary_en_vi.jsonl")

REQUIRED_KEYS = {
    "source_term",
    "target_term",
    "source_language",
    "target_language",
    "domain",
    "audience",
    "keep_verbatim",
}


def load_entries(path: Path) -> list[dict]:
    """Read the file, rejecting a malformed line rather than skipping it.

    Skipping would leave a glossary that is quietly missing a term, which shows
    up much later as an inconsistent translation nobody can explain.
    """
    entries: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise SystemExit(f"{path}:{number}: not valid JSON — {exc}") from exc
        missing = REQUIRED_KEYS - set(entry)
        if missing:
            raise SystemExit(f"{path}:{number}: missing {', '.join(sorted(missing))}")
        entries.append(entry)
    return entries


async def upsert(
    session: AsyncSession, entry: dict, *, embed_terms: bool
) -> tuple[GlossaryEntry, bool]:
    """Insert an entry, or update the one already occupying its scope.

    Returns the row and whether it was newly created.
    """
    normalized = normalize_term(entry["source_term"])
    existing = await session.scalar(
        select(GlossaryEntry).where(
            GlossaryEntry.source_term_normalized == normalized,
            GlossaryEntry.source_language == entry["source_language"],
            GlossaryEntry.target_language == entry["target_language"],
            GlossaryEntry.domain == entry["domain"],
            GlossaryEntry.audience == entry["audience"],
        )
    )

    row = existing or GlossaryEntry(
        source_term_normalized=normalized,
        source_language=entry["source_language"],
        target_language=entry["target_language"],
        domain=entry["domain"],
        audience=entry["audience"],
    )
    row.source_term = entry["source_term"]
    row.target_term = entry["target_term"]
    row.keep_verbatim = bool(entry["keep_verbatim"])
    # Re-running is how a retired entry is brought back, which is the only way
    # to undo a retirement without editing the database by hand.
    row.status = "active"

    if embed_terms and row.embedding is None:
        vector = await embed(entry["source_term"])
        if vector is not None:
            row.embedding = vector
            row.embedding_model = embedding_model_name()

    if existing is None:
        session.add(row)
    return row, existing is None


async def seed(args: argparse.Namespace) -> tuple[int, int]:
    """Load every entry. Returns (created, updated)."""
    entries = load_entries(args.file)
    print(f"Read {len(entries)} entries from {args.file}")

    created = updated = 0
    session_factory = get_async_session_maker()
    async with session_factory() as session:
        for entry in entries:
            _, is_new = await upsert(session, entry, embed_terms=not args.no_embed)
            created += is_new
            updated += not is_new
        await session.commit()
    return created, updated


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description="Load the starter glossary.")
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help=f"JSONL file to load. Default {DEFAULT_FILE}.",
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help=(
            "Skip embedding. Entries still match exactly; they will not be "
            "found by a wording the glossary has never seen."
        ),
    )
    args = parser.parse_args()

    if not args.file.exists():
        raise SystemExit(f"{args.file} does not exist")

    configure_logging(get_settings())
    created, updated = asyncio.run(seed(args))
    print(f"{created} entries created, {updated} updated")


if __name__ == "__main__":
    main()
