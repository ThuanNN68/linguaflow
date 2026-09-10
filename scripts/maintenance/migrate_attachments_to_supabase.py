"""Copy existing local attachments to the private Supabase Storage bucket.

Run from the repository root:

    python -m scripts.maintenance.migrate_attachments_to_supabase

Local files are retained by default. Add ``--delete-local-after-verify`` only
after the script has uploaded each object and verified its SHA-256 digest by
downloading it again.
"""

import argparse
import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select

from src.config import get_settings
from src.database import get_async_session_maker, get_engine
from src.database.models import Attachment

logger = logging.getLogger("migrate_attachments_to_supabase")


@dataclass
class MigrationCounts:
    uploaded: int = 0
    already_present: int = 0
    missing_local: int = 0
    failed: int = 0
    deleted_local: int = 0


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object_url(conversation_id: str, attachment_id: str) -> str:
    settings = get_settings()
    return (
        f"{settings.supabase_url.rstrip('/')}/storage/v1/object/"
        f"{settings.supabase_storage_bucket}/{conversation_id}/{attachment_id}"
    )


def _headers(content_type: str | None = None) -> dict[str, str]:
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "apikey": settings.supabase_service_role_key,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _local_path(attachment: Attachment) -> Path:
    root = Path(get_settings().upload_dir).resolve()
    candidate = (root / attachment.conversation_id / attachment.id).resolve()
    if root not in candidate.parents:
        raise ValueError(f"Unsafe attachment path: {attachment.id}")
    return candidate


async def _remote_payload(client: httpx.AsyncClient, attachment: Attachment) -> bytes | None:
    response = await client.get(
        _object_url(attachment.conversation_id, attachment.id),
        headers=_headers(),
    )
    if response.status_code == 404:
        return None
    if response.status_code == 400:
        try:
            detail = response.json()
        except ValueError:
            detail = {}
        if detail.get("code") == "NoSuchKey" or detail.get("error") == "not_found":
            return None
    response.raise_for_status()
    return response.content


async def migrate(*, delete_local_after_verify: bool) -> MigrationCounts:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured"
        )

    counts = MigrationCounts()
    session_maker = get_async_session_maker()
    async with session_maker() as db:
        attachments = (await db.scalars(select(Attachment))).all()

    async with httpx.AsyncClient(timeout=60) as client:
        for attachment in attachments:
            try:
                local_path = _local_path(attachment)
                if not local_path.is_file():
                    logger.error("MISSING  %s", local_path)
                    counts.missing_local += 1
                    continue

                local_payload = local_path.read_bytes()
                local_digest = _digest(local_payload)
                remote_payload = await _remote_payload(client, attachment)

                if remote_payload is None:
                    response = await client.post(
                        _object_url(attachment.conversation_id, attachment.id),
                        content=local_payload,
                        headers=_headers(attachment.content_type),
                    )
                    response.raise_for_status()
                    remote_payload = await _remote_payload(client, attachment)
                    counts.uploaded += 1
                else:
                    counts.already_present += 1

                if remote_payload is None or _digest(remote_payload) != local_digest:
                    raise RuntimeError("remote SHA-256 verification failed")

                logger.info("VERIFIED %s", local_path)
                if delete_local_after_verify:
                    local_path.unlink()
                    counts.deleted_local += 1
                    logger.info("DELETED  %s", local_path)
            except Exception:
                counts.failed += 1
                logger.exception("FAILED   attachment %s", attachment.id)

    await get_engine().dispose()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete-local-after-verify",
        action="store_true",
        help="Delete each local file only after the downloaded object has the same SHA-256 digest.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        counts = asyncio.run(
            migrate(delete_local_after_verify=args.delete_local_after_verify)
        )
    except Exception:
        logger.exception("Migration could not start")
        return 1

    logger.info(
        "SUMMARY uploaded=%d already_present=%d missing_local=%d failed=%d deleted_local=%d",
        counts.uploaded,
        counts.already_present,
        counts.missing_local,
        counts.failed,
        counts.deleted_local,
    )
    return 1 if counts.failed or counts.missing_local else 0


if __name__ == "__main__":
    raise SystemExit(main())
