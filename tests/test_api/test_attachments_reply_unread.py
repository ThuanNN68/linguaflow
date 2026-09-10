"""Tests for attachments, replies and unread counts (docs/api/contract.md §3.7, §3.8)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.core.security import create_access_token
from src.database.models import Attachment, ConversationMember, Message
from tests.conftest import auth_headers_for_user


def _authenticate(socket, user) -> None:
    """Authenticate a test socket the same way the chat client does."""
    socket.send_json({"type": "auth", "token": create_access_token(subject=user.id)})
    assert socket.receive_json() == {"type": "auth_ok", "user_id": user.id}


@pytest.mark.asyncio
async def test_uploaded_file_is_recorded_and_downloadable_by_another_member(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """An upload leaves a row, and membership alone grants the download."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])

    upload = await client.post(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
        files={"file": ("bao-cao.pdf", b"%PDF-1.4 test", "application/pdf")},
    )

    assert upload.status_code == 201
    body = upload.json()
    assert body["filename"] == "bao-cao.pdf"
    assert body["download_url"].endswith(body["id"])

    stored = await test_db.scalar(select(Attachment).where(Attachment.id == body["id"]))
    assert stored is not None
    assert stored.uploader_id == test_user.id
    # Not yet carried by any message: the file is uploaded before it is sent.
    assert stored.message_id is None

    download = await client.get(
        body["download_url"],
        headers=auth_headers_for_user(test_user_two),
    )
    assert download.status_code == 200
    assert download.content == b"%PDF-1.4 test"
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"] == 'attachment; filename="bao-cao.pdf"'


@pytest.mark.asyncio
async def test_an_outsider_cannot_download_an_attachment(
    client,
    test_user,
    test_user_headers,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    """Knowing the URL is not permission to read it."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    upload = await client.post(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
        files={"file": ("rieng-tu.txt", b"secret", "text/plain")},
    )

    response = await client.get(
        upload.json()["download_url"],
        headers=auth_headers_for_user(test_user_three),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_an_attachment_on_a_private_message_stays_private(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """The loose-upload exception must not expose a private message's file."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    upload = await client.post(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
        files={"file": ("private.txt", b"for the owner", "text/plain")},
    )
    assert upload.status_code == 201

    private_message = Message(
        client_message_id="private-attachment-1",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="Private attachment",
        visibility="private",
        visible_to_user_id=test_user.id,
    )
    test_db.add(private_message)
    await test_db.flush()
    attachment = await test_db.scalar(
        select(Attachment).where(Attachment.id == upload.json()["id"])
    )
    attachment.message_id = private_message.id
    await test_db.commit()

    hidden = await client.get(
        upload.json()["download_url"],
        headers=auth_headers_for_user(test_user_two),
    )
    visible = await client.get(
        upload.json()["download_url"],
        headers=test_user_headers,
    )

    assert hidden.status_code == 404
    assert visible.status_code == 200
    assert visible.content == b"for the owner"


@pytest.mark.asyncio
async def test_sending_with_an_attachment_binds_it_to_that_message(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    test_user_three,
    conversation_factory,
    ws_client,
):
    """The file stops being loose once a message carries it."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    upload = await client.post(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
        files={"file": ("ke-hoach.pdf", b"%PDF-1.4 plan", "application/pdf")},
    )
    attachment_id = upload.json()["id"]

    before_send = await client.get(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
    )
    assert before_send.status_code == 200
    assert before_send.json() == []

    test_client, _ = ws_client
    with test_client.websocket_connect("/api/v1/ws") as socket:
        _authenticate(socket, test_user)
        socket.send_json(
            {
                "type": "send_message",
                "client_message_id": "with-file-1",
                "conversation_id": conversation.id,
                "text": "Gửi anh bản kế hoạch",
                "attachment_id": attachment_id,
            }
        )
        acknowledgement = socket.receive_json()

    assert acknowledgement["type"] == "message_created"
    assert acknowledgement["message"]["attachment"]["id"] == attachment_id

    stored = await test_db.scalar(select(Attachment).where(Attachment.id == attachment_id))
    await test_db.refresh(stored)
    assert stored.message_id == acknowledgement["message"]["id"]

    history = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )
    assert history.json()[0]["attachment"]["filename"] == "ke-hoach.pdf"

    documents = await client.get(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=auth_headers_for_user(test_user_two),
    )
    assert documents.status_code == 200
    assert [document["id"] for document in documents.json()] == [attachment_id]
    assert documents.json()[0]["created_at"]

    outsider = await client.get(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=auth_headers_for_user(test_user_three),
    )
    assert outsider.status_code == 403


@pytest.mark.asyncio
async def test_a_reply_records_the_message_it_answers(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
    ws_client,
):
    """The quote has to survive a reload, so the link is stored, not local."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    parent = Message(
        client_message_id="parent-1",
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Mấy giờ họp?",
        created_at=datetime.now(UTC),
    )
    test_db.add(parent)
    await test_db.commit()

    test_client, _ = ws_client
    with test_client.websocket_connect("/api/v1/ws") as socket:
        _authenticate(socket, test_user)
        socket.send_json(
            {
                "type": "send_message",
                "client_message_id": "reply-1",
                "conversation_id": conversation.id,
                "text": "3 giờ chiều nhé",
                "reply_to_message_id": parent.id,
            }
        )
        acknowledgement = socket.receive_json()

    assert acknowledgement["message"]["reply_to_message_id"] == parent.id

    history = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )
    # Found by identity rather than by position: both messages land in the same
    # second, and SQLite's CURRENT_TIMESTAMP has no finer resolution, so the
    # order between them is decided by a random id.
    stored_reply = next(row for row in history.json() if row["client_message_id"] == "reply-1")
    assert stored_reply["reply_to_message_id"] == parent.id


@pytest.mark.asyncio
async def test_a_reply_pointing_outside_the_conversation_is_dropped(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
    ws_client,
):
    """A quote must never render text from a conversation the reader cannot see."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    elsewhere = await conversation_factory(test_user, [test_user, test_user_two])
    foreign = Message(
        client_message_id="foreign-1",
        conversation_id=elsewhere.id,
        sender_id=test_user.id,
        original_text="Chuyện của hội thoại khác",
        created_at=datetime.now(UTC),
    )
    test_db.add(foreign)
    await test_db.commit()

    test_client, _ = ws_client
    with test_client.websocket_connect("/api/v1/ws") as socket:
        _authenticate(socket, test_user)
        socket.send_json(
            {
                "type": "send_message",
                "client_message_id": "reply-foreign",
                "conversation_id": conversation.id,
                "text": "Trả lời nhầm chỗ",
                "reply_to_message_id": foreign.id,
            }
        )
        acknowledgement = socket.receive_json()

    # The message still goes through; only the bad link is discarded.
    assert acknowledgement["type"] == "message_created"
    assert acknowledgement["message"]["reply_to_message_id"] is None


@pytest.mark.asyncio
async def test_unread_counts_only_other_peoples_messages(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Your own messages are never unread, and neither are withdrawn ones."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    now = datetime.now(UTC)
    test_db.add_all(
        [
            Message(
                client_message_id="theirs-1",
                conversation_id=conversation.id,
                sender_id=test_user_two.id,
                original_text="Tin của người khác",
                created_at=now,
            ),
            Message(
                client_message_id="mine-1",
                conversation_id=conversation.id,
                sender_id=test_user.id,
                original_text="Tin của tôi",
                created_at=now + timedelta(seconds=1),
            ),
            Message(
                client_message_id="theirs-deleted",
                conversation_id=conversation.id,
                sender_id=test_user_two.id,
                original_text="",
                created_at=now + timedelta(seconds=2),
                deleted_at=now + timedelta(seconds=3),
            ),
        ]
    )
    await test_db.commit()

    listed = (await client.get("/api/v1/conversations", headers=test_user_headers)).json()[0]

    assert listed["unread_count"] == 1


@pytest.mark.asyncio
async def test_marking_read_clears_the_count_and_records_the_moment(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Opening a conversation is what moves the read mark forward."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    test_db.add(
        Message(
            client_message_id="unread-1",
            conversation_id=conversation.id,
            sender_id=test_user_two.id,
            original_text="Đọc đi",
            created_at=datetime.now(UTC),
        )
    )
    await test_db.commit()

    response = await client.post(
        f"/api/v1/conversations/{conversation.id}/read",
        headers=test_user_headers,
    )
    listed = (await client.get("/api/v1/conversations", headers=test_user_headers)).json()[0]

    assert response.status_code == 200
    assert response.json()["unread_count"] == 0
    assert listed["unread_count"] == 0

    membership = await test_db.scalar(
        select(ConversationMember).where(
            ConversationMember.conversation_id == conversation.id,
            ConversationMember.user_id == test_user.id,
        )
    )
    await test_db.refresh(membership)
    assert membership.last_read_at is not None


@pytest.mark.asyncio
async def test_marking_read_tells_the_other_members(
    client,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
    ws_client,
):
    """The sender's tick can only become "seen" if somebody says so."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user_two)
        response = test_client.post(
            f"/api/v1/conversations/{conversation.id}/read",
            headers=test_user_headers,
        )
        receipt = sender.receive_json()

    assert response.status_code == 200
    assert receipt["type"] == "message_read"
    assert receipt["conversation_id"] == conversation.id
    assert receipt["user_id"] == test_user.id
    assert receipt["read_at"].endswith("Z")


@pytest.mark.asyncio
async def test_a_non_member_cannot_mark_a_conversation_read(
    client,
    test_user,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    """Read state belongs to members only."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])

    response = await client.post(
        f"/api/v1/conversations/{conversation.id}/read",
        headers=auth_headers_for_user(test_user_three),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_an_attachment_on_a_private_reply_is_downloadable_by_its_reader(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """The visibility filter has to let the one person it was written for through."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])

    upload = await client.post(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
        files={"file": ("rieng-tu.txt", b"chi minh doc", "text/plain")},
    )
    assert upload.status_code == 201
    attachment_id = upload.json()["id"]

    private = Message(
        conversation_id=conversation.id,
        client_message_id="private-attach-1",
        sender_id=test_user.id,
        original_text="Bao cao rieng cua ban",
        source_language="vi",
        visibility="private",
        visible_to_user_id=test_user.id,
    )
    test_db.add(private)
    await test_db.flush()
    attachment = await test_db.scalar(
        select(Attachment).where(Attachment.id == attachment_id)
    )
    attachment.message_id = private.id
    await test_db.commit()

    download = await client.get(
        upload.json()["download_url"], headers=test_user_headers
    )

    assert download.status_code == 200
    assert download.content == b"chi minh doc"


@pytest.mark.asyncio
async def test_an_attachment_on_a_private_reply_is_hidden_from_the_other_member(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Membership is not enough once a private message carries the file.

    Written because the download query was widened to an OUTER join so that an
    attachment uploaded but not yet sent stays reachable. That is the right fix
    for the composer, and it must not become a way past ADR-31: a file carried by
    a private assistant reply belongs to the one person it was written for, and
    another member of the same conversation gets 404 — not 403, which would
    confirm the attachment exists.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])

    upload = await client.post(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
        files={"file": ("rieng-tu.txt", b"chi minh doc", "text/plain")},
    )
    assert upload.status_code == 201
    attachment_id = upload.json()["id"]

    private = Message(
        conversation_id=conversation.id,
        client_message_id="private-attach-2",
        sender_id=test_user.id,
        original_text="Bao cao rieng cua ban",
        source_language="vi",
        visibility="private",
        visible_to_user_id=test_user.id,
    )
    test_db.add(private)
    await test_db.flush()
    attachment = await test_db.scalar(
        select(Attachment).where(Attachment.id == attachment_id)
    )
    attachment.message_id = private.id
    await test_db.commit()

    download = await client.get(
        upload.json()["download_url"],
        headers=auth_headers_for_user(test_user_two),
    )

    assert download.status_code == 404
