"""Build the assistant's chunk index for conversations that predate it.

    python scripts/maintenance/backfill_assistant_chunks.py --dry-run
    python scripts/maintenance/backfill_assistant_chunks.py --limit 20
    python scripts/maintenance/backfill_assistant_chunks.py --conversation-id <id> --strategy token_window
    python scripts/maintenance/backfill_assistant_chunks.py --resume

Without this, the assistant's semantic recall is empty for every conversation
that existed before `assistant_chunks` did, and empty looks exactly like a
conversation in which nothing was said — the same honest cost `message_memory`
documents for turning `RAG_CONTEXT_ENABLED` on.

The message path keeps the index current by itself (`schedule_chunk_index`), so
this is for history and for the periodic full rebuild that removes the seams the
incremental pass leaves behind.

**Costs quota.** One embedding call per chunk. A conversation of two thousand
messages is roughly three hundred chunks under `turn_window`, and Gemini's free
tier is twenty calls a day — set `ASSISTANT_EMBEDDING_PROVIDER=local` before
running this over real history, or run it in batches with `--limit`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import distinct, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from src.config import configure_logging, get_settings  # noqa: E402
from src.database.models import (  # noqa: E402
    ASSISTANT_CHUNK_STRATEGIES,
    AssistantChunk,
    Message,
)
from src.services.assistant.assistant_indexing import index_conversation  # noqa: E402
from src.services.messaging.message_visibility import public_only  # noqa: E402
from src.services.shared.embeddings import (  # noqa: E402
    assistant_embedding_settings,
    embedding_model_name,
)

# Where `--resume` remembers what it has already done. A file rather than a
# column: this is operator state about one run, not something the application
# reads, and putting it in the schema would mean a migration for a script.
PROGRESS_FILE = PROJECT_ROOT / "data" / ".assistant_backfill_progress.json"


async def _conversations_needing_index(
    session, *, strategy: str, model: str, limit: int | None
) -> list[tuple[str, int]]:
    """Conversations with indexable messages, largest first, and their sizes.

    Largest first because that is where the index earns its keep: a
    twelve-message conversation is answered by the recent window alone, and
    spending the first of a limited quota on it means the two-thousand-message
    thread — the one nobody can search by hand — stays invisible.
    """
    indexed = select(distinct(AssistantChunk.conversation_id)).where(
        AssistantChunk.strategy == strategy,
        AssistantChunk.embedding_model == model,
    )
    rows = await session.execute(
        select(Message.conversation_id, func.count().label("messages"))
        .where(
            Message.deleted_at.is_(None),
            Message.original_text != "",
            public_only(),
            Message.conversation_id.not_in(indexed),
        )
        .group_by(Message.conversation_id)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [(row[0], row[1]) for row in rows.all()]


def _load_progress() -> set[str]:
    try:
        return set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_progress(done: set[str]) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(
        json.dumps(sorted(done), indent=1), encoding="utf-8"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy",
        default="turn_window",
        choices=ASSISTANT_CHUNK_STRATEGIES,
        help="Chiến lược chunk cần dựng (mặc định: turn_window, bản production dùng)",
    )
    parser.add_argument(
        "--conversation-id",
        action="append",
        default=[],
        help="Chỉ xử lý hội thoại này. Lặp lại cờ để nêu nhiều hội thoại.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Số hội thoại tối đa trong lần chạy này"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Bỏ qua các hội thoại lần chạy trước đã xong (đọc tệp tiến độ)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ liệt kê việc sẽ làm và chi phí ước tính, không gọi provider",
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    model = embedding_model_name(assistant_embedding_settings(settings))

    engine = create_async_engine(settings.database_url, echo=False)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    done = _load_progress() if args.resume else set()
    processed = 0
    total_chunks = 0
    started = time.perf_counter()

    try:
        async with session_maker() as session:
            if args.conversation_id:
                targets = [(cid, 0) for cid in args.conversation_id]
            else:
                targets = await _conversations_needing_index(
                    session, strategy=args.strategy, model=model, limit=args.limit
                )
            targets = [(cid, size) for cid, size in targets if cid not in done]

            if not targets:
                print("Không có hội thoại nào cần dựng chỉ mục.")
                return 0

            messages = sum(size for _, size in targets)
            print(
                f"{len(targets)} hội thoại · {messages} tin · "
                f"chiến lược={args.strategy} · model={model}"
            )
            if args.dry_run:
                for conversation_id, size in targets:
                    print(f"  {conversation_id}  {size} tin")
                print(
                    "\nƯớc tính: mỗi chunk là một lời gọi embedding. "
                    "Với turn_window thường khoảng 1 chunk cho mỗi 6-8 tin."
                )
                return 0

            for conversation_id, size in targets:
                try:
                    written = await index_conversation(
                        session,
                        conversation_id=conversation_id,
                        strategy=args.strategy,
                        settings=settings,
                    )
                except Exception as exc:  # noqa: BLE001
                    # One conversation failing must not abandon the rest: a
                    # quota limit hit halfway through is the expected way this
                    # ends, and everything indexed before it is still good.
                    print(f"  ✗ {conversation_id}: {type(exc).__name__}: {exc}")
                    continue

                done.add(conversation_id)
                processed += 1
                total_chunks += written
                print(f"  ✓ {conversation_id}  {size} tin → {written} chunk")
                _save_progress(done)
    finally:
        await engine.dispose()

    elapsed = time.perf_counter() - started
    print(
        f"\nXong {processed} hội thoại, {total_chunks} chunk, {elapsed:.0f}s. "
        f"Tiến độ ghi ở {PROGRESS_FILE.name} — dùng --resume để chạy tiếp."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
