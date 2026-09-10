"""Search and explicit translation-retry API tests."""

from datetime import UTC, datetime

import pytest

from src.api import chat as routes_module
from src.database.models import Message, TranslationResult
from src.services.language.translation import _persist_translation
from tests.conftest import auth_headers_for_user


async def _seed_message(
    test_db,
    *,
    conversation_id: str,
    sender_id: str,
    original_text: str = "Cuộc họp bắt đầu lúc ba giờ",
    deleted: bool = False,
) -> Message:
    message = Message(
        client_message_id=f"search-{sender_id[:8]}-{datetime.now(UTC).timestamp()}",
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text=original_text,
        source_language="vi",
        created_at=datetime.now(UTC),
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    test_db.add(message)
    await test_db.commit()
    return message


@pytest.mark.asyncio
async def test_search_matches_a_reader_visible_translation(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(
        test_db, conversation_id=conversation.id, sender_id=test_user_two.id
    )
    test_db.add(
        TranslationResult(
            message_id=message.id,
            target_language="en",
            translated_text="The meeting starts at three o'clock",
            model="test",
            latency_ms=1,
            is_fallback=False,
        )
    )
    await test_db.commit()

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=test_user_headers,
        params={"q": "starts at three"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["message"]["id"] == message.id
    assert body["items"][0]["matched_in"] == "translation"
    assert "starts at three" in body["items"][0]["snippet"]


@pytest.mark.asyncio
async def test_search_excludes_withdrawn_messages_and_refuses_outsiders(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    await _seed_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="this should not be found",
        deleted=True,
    )

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=test_user_headers,
        params={"q": "found"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []

    outsider = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=auth_headers_for_user(test_user_three),
        params={"q": "found"},
    )
    assert outsider.status_code == 403


@pytest.mark.asyncio
async def test_retry_translation_is_member_scoped_and_scheduled(
    client,
    monkeypatch,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(
        test_db, conversation_id=conversation.id, sender_id=test_user_two.id
    )
    captured: dict[str, object] = {}

    def schedule(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(routes_module, "schedule_translation_retry", schedule)
    response = await client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}/translate",
        headers=test_user_headers,
    )

    assert response.status_code == 202
    assert response.json() == {"message_id": message.id, "status": "scheduled"}
    assert captured["reader_id"] == test_user.id
    assert captured["message"] is not None

    outsider = await client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}/translate",
        headers=auth_headers_for_user(test_user_three),
    )
    assert outsider.status_code == 403


@pytest.mark.asyncio
async def test_forced_retry_creates_new_translation_row_preserving_history(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    from sqlalchemy import select

    from src.database.models import Feedback

    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(
        test_db, conversation_id=conversation.id, sender_id=test_user_two.id
    )
    # 1. First translation (version 1)
    v1 = await _persist_translation(
        test_db,
        message_id=message.id,
        target_language="en",
        honorific_profile="peer",
        translation_tone="natural",
        translated_text="Old wording (v1)",
        model="old-model",
        latency_ms=10,
        is_fallback=True,
        force=False,
    )
    assert v1.version == 1

    # Attach feedback to v1
    feedback = Feedback(
        translation_id=v1.id,
        user_id=test_user.id,
        rating=2,
        correction="Inaccurate translation",
    )
    test_db.add(feedback)
    await test_db.commit()

    # 2. Forced retry (Translate Again) -> creates version 2
    v2 = await _persist_translation(
        test_db,
        message_id=message.id,
        target_language="en",
        honorific_profile="peer",
        translation_tone="natural",
        translated_text="Fresh wording (v2)",
        model="new-model",
        latency_ms=20,
        is_fallback=False,
        force=True,
    )

    assert v2 is not None
    assert v2.id != v1.id
    assert v2.version == 2
    assert v2.translated_text == "Fresh wording (v2)"

    # Verify v1 and its feedback are completely preserved
    old_row = await test_db.get(TranslationResult, v1.id)
    assert old_row is not None
    assert old_row.translated_text == "Old wording (v1)"
    assert old_row.version == 1

    saved_fb = await test_db.scalar(select(Feedback).where(Feedback.translation_id == v1.id))
    assert saved_fb is not None
    assert saved_fb.rating == 2

    # 3. Verify reader API query returns v2 (the newest translation)
    messages_res = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )
    assert messages_res.status_code == 200
    msg_data = messages_res.json()[0]
    en_translation = next(t for t in msg_data["translations"] if t["target_language"] == "en")
    assert en_translation["translation_id"] == v2.id
    assert en_translation["translated_text"] == "Fresh wording (v2)"


@pytest.mark.asyncio
async def test_normal_translation_persistence_is_idempotent(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    """Normal translation (force=False) creates version 1 and subsequent calls return version 1."""
    from sqlalchemy import func, select

    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(
        test_db, conversation_id=conversation.id, sender_id=test_user_two.id
    )

    first = await _persist_translation(
        test_db,
        message_id=message.id,
        target_language="en",
        honorific_profile="peer",
        translation_tone="natural",
        translated_text="Idempotent result",
        model="model-a",
        latency_ms=10,
        is_fallback=False,
        force=False,
    )
    assert first.version == 1

    # Second normal persistence call for same bucket
    second = await _persist_translation(
        test_db,
        message_id=message.id,
        target_language="en",
        honorific_profile="peer",
        translation_tone="natural",
        translated_text="Duplicate result",
        model="model-a",
        latency_ms=10,
        is_fallback=False,
        force=False,
    )
    assert second.id == first.id
    assert second.version == 1

    # Total rows in DB must be exactly 1
    count = await test_db.scalar(
        select(func.count(TranslationResult.id)).where(TranslationResult.message_id == message.id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_repeated_retries_increment_versions_and_render_latest(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Retries A -> B -> C increment version 1 -> 2 -> 3; API renders latest version 3."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = await _seed_message(
        test_db, conversation_id=conversation.id, sender_id=test_user_two.id
    )

    t1 = await _persist_translation(
        test_db,
        message_id=message.id,
        target_language="en",
        honorific_profile="peer",
        translation_tone="natural",
        translated_text="Version 1 wording",
        model="model-1",
        latency_ms=10,
        is_fallback=False,
        force=False,
    )
    assert t1.version == 1

    t2 = await _persist_translation(
        test_db,
        message_id=message.id,
        target_language="en",
        honorific_profile="peer",
        translation_tone="natural",
        translated_text="Version 2 wording",
        model="model-2",
        latency_ms=12,
        is_fallback=False,
        force=True,
    )
    assert t2.version == 2

    t3 = await _persist_translation(
        test_db,
        message_id=message.id,
        target_language="en",
        honorific_profile="peer",
        translation_tone="natural",
        translated_text="Version 3 wording",
        model="model-3",
        latency_ms=15,
        is_fallback=False,
        force=True,
    )
    assert t3.version == 3

    # API returns Version 3 for reader
    res = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )
    assert res.status_code == 200
    msg = res.json()[0]
    en_t = next(t for t in msg["translations"] if t["target_language"] == "en")
    assert en_t["translation_id"] == t3.id
    assert en_t["translated_text"] == "Version 3 wording"


@pytest.mark.asyncio
async def test_search_collects_full_limit_across_wrong_tone_candidates(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Search matches SQL on target_language='en' but discards non-matching tone/honorific candidates in Python,

    verifying that valid later candidates are still collected up to limit without being starved.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    # test_user has preferred_language='en', default profile='peer', tone='natural'

    # Seed 3 messages in chronological order: msg1 (T1), msg2 (T2), msg3 (T3)
    # msg1: has peer/natural 'en' translation containing "alpha-keyword"
    msg1 = await _seed_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Vietnamese text 1",
    )
    test_db.add(
        TranslationResult(
            message_id=msg1.id,
            target_language="en",
            honorific_profile="peer",
            translation_tone="natural",
            version=1,
            translated_text="This is alpha-keyword for peer reader",
            model="test",
            latency_ms=1,
            is_fallback=False,
        )
    )

    # msg2: has 'en' translation matching "alpha-keyword" in SQL, BUT for senior/formal profile/tone.
    # Its peer/natural translation does NOT contain "alpha-keyword".
    # Therefore, SQL matches msg2 on target_language=='en' & text.ilike('%alpha-keyword%'),
    # but select_for_reader picks the peer/natural translation ("Irrelevant content") and Python discards it.
    msg2 = await _seed_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Vietnamese text 2",
    )
    test_db.add_all([
        TranslationResult(
            message_id=msg2.id,
            target_language="en",
            honorific_profile="senior",
            translation_tone="formal",
            version=1,
            translated_text="Confidential alpha-keyword for senior manager",
            model="test",
            latency_ms=1,
            is_fallback=False,
        ),
        TranslationResult(
            message_id=msg2.id,
            target_language="en",
            honorific_profile="peer",
            translation_tone="natural",
            version=1,
            translated_text="Irrelevant plain content for peer",
            model="test",
            latency_ms=1,
            is_fallback=False,
        ),
    ])

    # msg3: has peer/natural 'en' translation containing "alpha-keyword"
    msg3 = await _seed_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Vietnamese text 3",
    )
    test_db.add(
        TranslationResult(
            message_id=msg3.id,
            target_language="en",
            honorific_profile="peer",
            translation_tone="natural",
            version=1,
            translated_text="Another alpha-keyword notice for peer reader",
            model="test",
            latency_ms=1,
            is_fallback=False,
        )
    )
    await test_db.commit()

    # Search with limit=2 for "alpha-keyword".
    # Candidates in DB ordered created_at desc: msg3, msg2, msg1.
    # msg3 matches reader translation -> accepted (1/2)
    # msg2 matches SQL but reader translation doesn't have keyword -> discarded
    # msg1 matches reader translation -> accepted (2/2)
    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=test_user_headers,
        params={"q": "alpha-keyword", "limit": 2},
    )

    assert response.status_code == 200
    data = response.json()
    returned_ids = [item["message"]["id"] for item in data["items"]]
    assert len(returned_ids) == 2
    assert data["has_more"] is False


@pytest.mark.asyncio
async def test_search_with_limit_51_and_100_succeeds_without_validation_error(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    """Verifies that public search limit=51 and limit=100 execute cleanly without internal limit validation failure."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    msg = await _seed_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Searchable payload keyword item",
    )
    await test_db.commit()

    # Test limit=51 (previously generated batch_size=102 which violated ChatService limit <= 100)
    res_51 = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=test_user_headers,
        params={"q": "payload", "limit": 51},
    )
    assert res_51.status_code == 200
    data_51 = res_51.json()
    assert len(data_51["items"]) == 1
    assert data_51["items"][0]["message"]["id"] == msg.id

    # Test limit=100 (previously generated batch_size=200 which violated ChatService limit <= 100)
    res_100 = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=test_user_headers,
        params={"q": "payload", "limit": 100},
    )
    assert res_100.status_code == 200
    data_100 = res_100.json()
    assert len(data_100["items"]) == 1
    assert data_100["items"][0]["message"]["id"] == msg.id
