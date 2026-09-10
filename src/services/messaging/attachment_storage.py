"""Shared byte storage for conversation attachments.

Authorization deliberately does not live here. Callers must first establish
that the attachment record is visible to the current operation, then pass that
known record to :meth:`AttachmentStorage.read`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import boto3
import httpx
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from src.config import Settings, get_settings
from src.database.models import Attachment

_STORAGE_TIMEOUT_SECONDS = 60


class AttachmentStorageError(RuntimeError):
    """The configured attachment store could not complete an operation."""


class AttachmentStorageNotFoundError(AttachmentStorageError):
    """The authorized attachment record has no corresponding stored object."""


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    """Attachment bytes paired with the metadata already stored in the database."""

    data: bytes
    filename: str
    content_type: str


@dataclass(frozen=True, slots=True)
class AttachmentDownload:
    """A route-ready local path or remote payload with persisted metadata."""

    filename: str
    content_type: str
    local_path: Path | None = None
    data: bytes | None = None


@dataclass(frozen=True, slots=True)
class PresignedAttachment:
    url: str
    expires_in_seconds: int


class AttachmentStorageProvider(Protocol):
    """Backend contract independent from authorization and database access."""

    async def write(
        self,
        *,
        conversation_id: str,
        attachment_id: str,
        data: bytes,
        content_type: str,
    ) -> None: ...

    async def download(self, attachment: Attachment) -> AttachmentDownload: ...

    async def delete(self, attachment: Attachment) -> None: ...


class LocalAttachmentProvider:
    """Filesystem backend for development and single-host installations."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _local_path(self, conversation_id: str, attachment_id: str) -> Path:
        root = Path(self._settings.upload_dir).resolve()
        candidate = (root / conversation_id / attachment_id).resolve()
        if root not in candidate.parents:
            raise AttachmentStorageNotFoundError("Attachment was not found")
        return candidate

    async def write(
        self,
        *,
        conversation_id: str,
        attachment_id: str,
        data: bytes,
        content_type: str,
    ) -> None:
        del content_type
        destination = self._local_path(conversation_id, attachment_id)
        try:
            await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(destination.write_bytes, data)
        except OSError:
            raise AttachmentStorageError("Unable to store attachment") from None

    async def download(self, attachment: Attachment) -> AttachmentDownload:
        path = self._local_path(attachment.conversation_id, attachment.id)
        if not await asyncio.to_thread(path.is_file):
            raise AttachmentStorageNotFoundError("Attachment was not found")
        return AttachmentDownload(
            local_path=path,
            filename=attachment.filename,
            content_type=attachment.content_type,
        )

    async def delete(self, attachment: Attachment) -> None:
        path = self._local_path(attachment.conversation_id, attachment.id)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            raise AttachmentStorageError("Unable to delete attachment") from None


class SupabaseAttachmentProvider:
    """Supabase Storage backend retained for existing hosted deployments."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    @staticmethod
    def _object_path(conversation_id: str, attachment_id: str) -> str:
        return f"{conversation_id}/{attachment_id}"

    def _object_url(self, object_path: str) -> str:
        return (
            f"{self._settings.supabase_url.rstrip('/')}/storage/v1/object/"
            f"{self._settings.supabase_storage_bucket}/{object_path}"
        )

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._settings.supabase_service_role_key}",
            "apikey": self._settings.supabase_service_role_key,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _object_is_missing(response: httpx.Response) -> bool:
        """Supabase may encode NoSuchKey as HTTP 400 with an inner status."""
        if response.status_code == 404:
            return True
        if response.status_code != 400:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        return payload.get("code") == "NoSuchKey" or payload.get("error") == "not_found"

    async def write(
        self,
        *,
        conversation_id: str,
        attachment_id: str,
        data: bytes,
        content_type: str,
    ) -> None:
        """Store bytes under the existing conversation/attachment key."""
        try:
            async with httpx.AsyncClient(
                timeout=_STORAGE_TIMEOUT_SECONDS,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._object_url(self._object_path(conversation_id, attachment_id)),
                    content=data,
                    headers=self._headers(content_type),
                )
            response.raise_for_status()
        except httpx.HTTPError:
            raise AttachmentStorageError("Unable to store attachment") from None

    async def download(self, attachment: Attachment) -> AttachmentDownload:
        try:
            async with httpx.AsyncClient(
                timeout=_STORAGE_TIMEOUT_SECONDS,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._object_url(self._object_path(attachment.conversation_id, attachment.id)),
                    headers=self._headers(),
                )
            if self._object_is_missing(response):
                raise AttachmentStorageNotFoundError("Attachment was not found")
            response.raise_for_status()
        except AttachmentStorageNotFoundError:
            raise
        except httpx.HTTPError:
            raise AttachmentStorageError("Unable to retrieve attachment") from None

        return AttachmentDownload(
            data=response.content,
            filename=attachment.filename,
            content_type=attachment.content_type,
        )

    async def delete(self, attachment: Attachment) -> None:
        try:
            async with httpx.AsyncClient(timeout=_STORAGE_TIMEOUT_SECONDS, transport=self._transport) as client:
                response = await client.delete(
                    self._object_url(self._object_path(attachment.conversation_id, attachment.id)),
                    headers=self._headers(),
                )
            if not self._object_is_missing(response):
                response.raise_for_status()
        except httpx.HTTPError:
            raise AttachmentStorageError("Unable to delete attachment") from None


class S3AttachmentProvider:
    """S3-compatible backend for AWS S3, Cloudflare R2, MinIO, and peers."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or self._build_client()

    def _build_client(self) -> Any:
        kwargs: dict[str, Any] = {
            "region_name": self._settings.s3_region,
            "config": Config(s3={"addressing_style": "path" if self._settings.s3_force_path_style else "auto"}),
        }
        if self._settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self._settings.s3_endpoint_url
        if self._settings.s3_access_key_id:
            kwargs["aws_access_key_id"] = self._settings.s3_access_key_id
            kwargs["aws_secret_access_key"] = self._settings.s3_secret_access_key
        if self._settings.s3_session_token:
            kwargs["aws_session_token"] = self._settings.s3_session_token
        return boto3.client("s3", **kwargs)

    @staticmethod
    def _key(conversation_id: str, attachment_id: str) -> str:
        return f"{conversation_id}/{attachment_id}"

    async def write(
        self,
        *,
        conversation_id: str,
        attachment_id: str,
        data: bytes,
        content_type: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._settings.s3_bucket,
                Key=self._key(conversation_id, attachment_id),
                Body=data,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError, OSError):
            raise AttachmentStorageError("Unable to store attachment") from None

    async def download(self, attachment: Attachment) -> AttachmentDownload:
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._settings.s3_bucket,
                Key=self._key(attachment.conversation_id, attachment.id),
            )
            body = response["Body"]
            data = await asyncio.to_thread(body.read)
            close = getattr(body, "close", None)
            if close is not None:
                await asyncio.to_thread(close)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise AttachmentStorageNotFoundError("Attachment was not found") from None
            raise AttachmentStorageError("Unable to retrieve attachment") from None
        except (BotoCoreError, KeyError, OSError):
            raise AttachmentStorageError("Unable to retrieve attachment") from None
        return AttachmentDownload(
            data=data,
            filename=attachment.filename,
            content_type=attachment.content_type,
        )

    async def delete(self, attachment: Attachment) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._settings.s3_bucket,
                Key=self._key(attachment.conversation_id, attachment.id),
            )
        except (BotoCoreError, ClientError, OSError):
            raise AttachmentStorageError("Unable to delete attachment") from None

    async def presign(self, attachment: Attachment) -> PresignedAttachment:
        ttl = self._settings.attachment_presign_ttl_seconds
        try:
            url = await asyncio.to_thread(
                self._client.generate_presigned_url,
                "get_object",
                Params={
                    "Bucket": self._settings.s3_bucket,
                    "Key": self._key(attachment.conversation_id, attachment.id),
                    "ResponseContentType": attachment.content_type,
                    "ResponseContentDisposition": f'attachment; filename="{attachment.filename}"',
                },
                ExpiresIn=ttl,
            )
        except (BotoCoreError, ClientError, OSError):
            raise AttachmentStorageError("Unable to create attachment URL") from None
        return PresignedAttachment(url=url, expires_in_seconds=ttl)


class AttachmentStorage:
    """Stable facade selecting one configured attachment-storage provider."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        provider: AttachmentStorageProvider | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self.backend = self._resolve_backend()
        self._provider = provider or self._build_provider(transport=transport, s3_client=s3_client)

    def _resolve_backend(self) -> str:
        configured = self._settings.attachment_storage_backend
        if configured != "auto":
            return configured
        if self._settings.s3_bucket:
            return "s3"
        if self._settings.supabase_url and self._settings.supabase_service_role_key:
            return "supabase"
        return "local"

    def _build_provider(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None,
        s3_client: Any | None,
    ) -> AttachmentStorageProvider:
        if self.backend == "s3":
            if not self._settings.s3_bucket:
                raise AttachmentStorageError("S3_BUCKET is required for the S3 attachment backend")
            return S3AttachmentProvider(self._settings, client=s3_client)
        if self.backend == "supabase":
            if not self._settings.supabase_url or not self._settings.supabase_service_role_key:
                raise AttachmentStorageError(
                    "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for Supabase storage"
                )
            return SupabaseAttachmentProvider(self._settings, transport=transport)
        return LocalAttachmentProvider(self._settings)

    @property
    def uses_supabase(self) -> bool:
        return self.backend == "supabase"

    @property
    def uses_s3(self) -> bool:
        return self.backend == "s3"

    async def write(
        self,
        *,
        conversation_id: str,
        attachment_id: str,
        data: bytes,
        content_type: str,
    ) -> None:
        await self._provider.write(
            conversation_id=conversation_id,
            attachment_id=attachment_id,
            data=data,
            content_type=content_type,
        )

    async def download(self, attachment: Attachment) -> AttachmentDownload:
        """Resolve route-ready content without moving authorization into storage."""
        return await self._provider.download(attachment)

    async def delete(self, attachment: Attachment) -> None:
        """Permanently remove an authorized attachment object."""
        await self._provider.delete(attachment)

    async def presign(self, attachment: Attachment) -> PresignedAttachment | None:
        """Return a short-lived direct URL only for a private S3 object."""
        if not isinstance(self._provider, S3AttachmentProvider):
            return None
        return await self._provider.presign(attachment)

    async def read(self, attachment: Attachment) -> StoredAttachment:
        """Read bytes for an attachment record already authorized by the caller."""
        download = await self.download(attachment)
        if download.local_path is not None:
            try:
                data = await asyncio.to_thread(download.local_path.read_bytes)
            except FileNotFoundError:
                raise AttachmentStorageNotFoundError("Attachment was not found") from None
            except OSError:
                raise AttachmentStorageError("Unable to retrieve attachment") from None
        else:
            data = download.data if download.data is not None else b""

        return StoredAttachment(
            data=data,
            filename=download.filename,
            content_type=download.content_type,
        )
