"""Focused tests for the attachment byte-storage seam."""

from __future__ import annotations

import httpx
import pytest
from botocore.exceptions import ClientError

from src.config import Settings
from src.database.models import Attachment
from src.services.messaging.attachment_storage import (
    AttachmentStorage,
    AttachmentStorageError,
    AttachmentStorageNotFoundError,
)

VALID_SECRET = "x" * 48


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "jwt_secret": VALID_SECRET,
        "upload_dir": str(tmp_path),
        "supabase_url": "",
        "supabase_service_role_key": "",
    }
    values.update(overrides)
    return Settings(**values)


def _attachment(**overrides) -> Attachment:
    values = {
        "id": "attachment-1",
        "conversation_id": "conversation-1",
        "uploader_id": "user-1",
        "filename": "recording.webm",
        "content_type": "audio/webm",
        "size": 11,
    }
    values.update(overrides)
    return Attachment(**values)


@pytest.mark.asyncio
async def test_local_storage_round_trip_preserves_record_metadata(tmp_path):
    storage = AttachmentStorage(_settings(tmp_path))
    attachment = _attachment()

    await storage.write(
        conversation_id=attachment.conversation_id,
        attachment_id=attachment.id,
        data=b"audio-bytes",
        content_type=attachment.content_type,
    )
    stored = await storage.read(attachment)

    assert stored.data == b"audio-bytes"
    assert stored.filename == "recording.webm"
    assert stored.content_type == "audio/webm"


@pytest.mark.asyncio
async def test_local_storage_reports_a_missing_object(tmp_path):
    storage = AttachmentStorage(_settings(tmp_path))

    with pytest.raises(AttachmentStorageNotFoundError, match="not found"):
        await storage.read(_attachment())


@pytest.mark.asyncio
async def test_supabase_storage_round_trip_uses_existing_object_key_and_headers(tmp_path):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert await request.aread() == b"audio-bytes"
            return httpx.Response(200)
        return httpx.Response(200, content=b"stored-audio")

    settings = _settings(
        tmp_path,
        supabase_url="https://storage.example.test",
        supabase_service_role_key="service-key",
        supabase_storage_bucket="attachments",
    )
    storage = AttachmentStorage(settings, transport=httpx.MockTransport(handler))
    attachment = _attachment()

    await storage.write(
        conversation_id=attachment.conversation_id,
        attachment_id=attachment.id,
        data=b"audio-bytes",
        content_type=attachment.content_type,
    )
    stored = await storage.read(attachment)

    assert stored.data == b"stored-audio"
    assert [request.method for request in requests] == ["POST", "GET"]
    assert all(request.url.path == "/storage/v1/object/attachments/conversation-1/attachment-1" for request in requests)
    assert requests[0].headers["content-type"] == "audio/webm"
    assert requests[0].headers["authorization"] == "Bearer service-key"
    assert requests[1].headers["apikey"] == "service-key"


@pytest.mark.asyncio
async def test_supabase_missing_and_provider_failures_are_controlled(tmp_path):
    responses = iter(
        [
            httpx.Response(400, json={"code": "NoSuchKey"}),
            httpx.Response(503, text="raw storage provider detail"),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    settings = _settings(
        tmp_path,
        supabase_url="https://storage.example.test",
        supabase_service_role_key="service-key",
    )
    storage = AttachmentStorage(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(AttachmentStorageNotFoundError, match="not found"):
        await storage.read(_attachment())
    with pytest.raises(AttachmentStorageError, match="Unable to retrieve") as exc_info:
        await storage.read(_attachment())

    assert "raw storage provider detail" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_s3_compatible_storage_round_trip_uses_stable_object_key(tmp_path):
    objects: dict[tuple[str, str], bytes] = {}

    class Body:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def close(self) -> None:
            pass

    class Client:
        def put_object(self, **kwargs):
            objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
            assert kwargs["ContentType"] == "audio/webm"

        def get_object(self, **kwargs):
            key = (kwargs["Bucket"], kwargs["Key"])
            if key not in objects:
                raise ClientError(
                    {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                    "GetObject",
                )
            return {"Body": Body(objects[key])}

    settings = _settings(
        tmp_path,
        attachment_storage_backend="s3",
        s3_bucket="linguaflow-test",
        s3_endpoint_url="https://objects.example.test",
    )
    storage = AttachmentStorage(settings, s3_client=Client())
    attachment = _attachment()

    await storage.write(
        conversation_id=attachment.conversation_id,
        attachment_id=attachment.id,
        data=b"audio-bytes",
        content_type=attachment.content_type,
    )
    stored = await storage.read(attachment)

    assert storage.uses_s3 is True
    assert stored.data == b"audio-bytes"
    assert objects == {("linguaflow-test", "conversation-1/attachment-1"): b"audio-bytes"}


@pytest.mark.asyncio
async def test_s3_missing_object_maps_to_storage_not_found(tmp_path):
    class Client:
        def get_object(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "provider detail"}},
                "GetObject",
            )

    storage = AttachmentStorage(
        _settings(tmp_path, attachment_storage_backend="s3", s3_bucket="bucket"),
        s3_client=Client(),
    )

    with pytest.raises(AttachmentStorageNotFoundError, match="not found"):
        await storage.read(_attachment())


@pytest.mark.asyncio
async def test_s3_presign_is_short_lived_and_content_bound(tmp_path):
    captured: dict[str, object] = {}
    class Client:
        def generate_presigned_url(self, operation, **kwargs):
            captured.update(operation=operation, **kwargs)
            return "https://objects.example.test/signed"
    storage = AttachmentStorage(
        _settings(tmp_path, attachment_storage_backend="s3", s3_bucket="bucket", attachment_presign_ttl_seconds=120),
        s3_client=Client(),
    )
    signed = await storage.presign(_attachment())
    assert signed is not None
    assert signed.url == "https://objects.example.test/signed"
    assert signed.expires_in_seconds == 120
    assert captured["operation"] == "get_object"
    assert captured["ExpiresIn"] == 120
    assert captured["Params"]["Key"] == "conversation-1/attachment-1"
