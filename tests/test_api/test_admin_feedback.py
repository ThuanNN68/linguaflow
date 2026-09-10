"""Tests for the administrator's view of reader feedback (F-05).

Two things are guarded. The gate, because `users.role == "admin"` is the whole
authorization model and a missing dependency looks like working code. And the
boundary: an administrator may see counts and the two anonymised fragments
prepared at edit time, and nothing else — a correction whose author withheld
consent must be countable but never readable.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.database.models import (
    CorrectionLog,
    Feedback,
    Message,
    TranslationEdit,
    TranslationResult,
)


def response_text(body: dict) -> str:
    """The whole response as one string, for absence assertions."""
    return json.dumps(body, ensure_ascii=False)


async def _translation(test_db, conversation_id: str, sender_id: str) -> str:
    """Persist one message with one translation and return the translation id."""
    message = Message(
        client_message_id=f"fb-{sender_id[:8]}-{datetime.now(UTC).timestamp()}",
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text="Deploy xong chưa anh?",
        source_language="vi",
        created_at=datetime.now(UTC),
    )
    test_db.add(message)
    await test_db.commit()

    translation = TranslationResult(
        message_id=message.id,
        target_language="en",
        translated_text="Is the deploy done?",
        model="mistral-small-latest",
        latency_ms=640,
        is_fallback=False,
    )
    test_db.add(translation)
    await test_db.commit()
    return translation.id


def _correction(user_id: str, *, consent: bool, phrase: str, minutes: int = 0):
    """One row of the miner's evidence, consented or not."""
    return CorrectionLog(
        source_phrase=phrase,
        corrected_target="môi trường staging",
        source_language="en",
        target_language="vi",
        domain="engineering",
        audience="internal",
        user_id=user_id,
        consent_to_share=consent,
        anonymized_snippet=f"… {phrase} …",
        original_snippet="Can you deploy to the staging environment",
        observed_at=datetime.now(UTC) - timedelta(minutes=minutes),
    )


@pytest.mark.asyncio
async def test_a_member_cannot_read_the_feedback_overview(client, test_user_headers):
    """The gate is one string comparison per endpoint, so each one needs its own."""
    response = await client.get("/api/v1/admin/feedback", headers=test_user_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_rating_the_interface_never_sends_is_still_visible_in_the_histogram(
    client, test_db, test_admin_headers, test_user, test_user_two, conversation_factory
):
    """The interface sends 5 and 1, and those are the two named buckets. A 3 is
    not given a bucket of its own — there is no third answer to offer — but it
    has to stay countable, because a value outside the two is how a change in
    the interface would announce itself."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    first = await _translation(test_db, conversation.id, test_user_two.id)
    second = await _translation(test_db, conversation.id, test_user_two.id)

    test_db.add(Feedback(translation_id=first, user_id=test_user.id, rating=5))
    test_db.add(Feedback(translation_id=second, user_id=test_user.id, rating=1))
    test_db.add(Feedback(translation_id=first, user_id=test_user_two.id, rating=3))
    await test_db.commit()

    response = await client.get("/api/v1/admin/feedback", headers=test_admin_headers)

    assert response.status_code == 200
    votes = response.json()["votes"]
    assert (votes["up"], votes["down"]) == (1, 1)
    assert "neutral" not in votes
    assert votes["total"] == 3
    assert votes["up_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert votes["ratings"] == {"5": 1, "3": 1, "1": 1}


@pytest.mark.asyncio
async def test_a_correction_without_consent_is_counted_but_never_shown(
    client, test_db, test_admin_headers, test_user
):
    """Consent decides whether the wording may be read, not whether the row
    exists. Counting the withheld ones is what makes it visible that they are
    being left out rather than silently missing."""
    test_db.add(_correction(test_user.id, consent=True, phrase="staging environment"))
    test_db.add(_correction(test_user.id, consent=False, phrase="secret wording"))
    await test_db.commit()

    body = (
        await client.get("/api/v1/admin/feedback", headers=test_admin_headers)
    ).json()

    assert body["shared_total"] == 1
    assert body["withheld_total"] == 1
    phrases = [row["source_phrase"] for row in body["shared_corrections"]]
    assert phrases == ["staging environment"]
    assert "secret wording" not in response_text(body)


@pytest.mark.asyncio
async def test_a_shared_correction_carries_no_author_or_conversation(
    client, test_db, test_admin_headers, test_user
):
    """The DTO having nowhere to put them is what keeps the rule true; this
    test is what notices when a column is added back."""
    test_db.add(_correction(test_user.id, consent=True, phrase="staging environment"))
    await test_db.commit()

    body = (
        await client.get("/api/v1/admin/feedback", headers=test_admin_headers)
    ).json()

    row = body["shared_corrections"][0]
    assert set(row) == {
        "source_phrase",
        "corrected_target",
        "source_language",
        "target_language",
        "domain",
        "audience",
        "original_snippet",
        "anonymized_snippet",
        "observed_at",
    }
    # `original_snippet` is what the sender actually wrote, in whichever
    # language that was — the other side of the translation the correction
    # came from, which `anonymized_snippet` alone never showed.
    assert row["original_snippet"] == "Can you deploy to the staging environment"
    assert test_user.id not in response_text(body)


@pytest.mark.asyncio
async def test_shared_corrections_are_newest_first(
    client, test_db, test_admin_headers, test_user
):
    """A term the team started arguing about today is worth more attention than
    one from six weeks ago, the same way the proposal queue is ordered."""
    test_db.add(_correction(test_user.id, consent=True, phrase="older", minutes=90))
    test_db.add(_correction(test_user.id, consent=True, phrase="newer", minutes=1))
    await test_db.commit()

    body = (
        await client.get("/api/v1/admin/feedback", headers=test_admin_headers)
    ).json()

    assert [row["source_phrase"] for row in body["shared_corrections"]] == [
        "newer",
        "older",
    ]


@pytest.mark.asyncio
async def test_review_queue_shows_anonymous_votes_and_edits_with_translation_context(
    client, test_db, test_admin_headers, test_user, test_user_two, conversation_factory
):
    """The quality table joins the two feedback stores without leaking identity.

    The feedback form writes a vote and optional short correction to
    ``feedbacks``. The pencil editor writes a separate append-only row to
    ``translation_edits``. Both need the original and machine translation when
    an administrator reviews why the reader disagreed with the model.
    """
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    translation_id = await _translation(test_db, conversation.id, test_user_two.id)
    test_db.add_all(
        [
            Feedback(
                translation_id=translation_id,
                user_id=test_user.id,
                rating=1,
                correction="Use a less formal question.",
            ),
            TranslationEdit(
                translation_id=translation_id,
                editor_id=test_user.id,
                edited_text="Is the deployment finished yet?",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    await test_db.commit()

    response = await client.get("/api/v1/admin/feedback", headers=test_admin_headers)

    assert response.status_code == 200
    entries = response.json()["review_entries"]
    vote = next(entry for entry in entries if entry["entry_type"] == "vote")
    edit = next(entry for entry in entries if entry["entry_type"] == "edit")

    assert vote == {
        "entry_type": "vote",
        "original_text": "Deploy xong chưa anh?",
        "translated_text": "Is the deploy done?",
        "source_language": "vi",
        "target_language": "en",
        "model": "mistral-small-latest",
        "vote": "down",
        "rating": 1,
        "user_correction": "Use a less formal question.",
        "created_at": vote["created_at"],
        "occurrence_count": 1,
    }
    assert edit["entry_type"] == "edit"
    assert edit["vote"] is None
    assert edit["user_correction"] == "Is the deployment finished yet?"
    assert edit["original_text"] == vote["original_text"]
    assert edit["translated_text"] == vote["translated_text"]
    assert response.json()["edited_translations"] == 1
    assert "user_id" not in vote
    assert "editor_id" not in edit
    assert test_user.id not in response_text(response.json())
    assert conversation.id not in response_text(response.json())


@pytest.mark.asyncio
async def test_review_queue_keeps_only_the_latest_edit_for_each_translation(
    client, test_db, test_admin_headers, test_user, test_user_two, conversation_factory
):
    """Repeated revisions must not look like duplicate quality signals."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    translation_id = await _translation(test_db, conversation.id, test_user_two.id)
    now = datetime.now(UTC)
    test_db.add_all(
        [
            TranslationEdit(
                translation_id=translation_id,
                editor_id=test_user.id,
                edited_text="Older wording",
                created_at=now - timedelta(minutes=1),
            ),
            TranslationEdit(
                translation_id=translation_id,
                editor_id=test_user.id,
                edited_text="Latest wording",
                created_at=now,
            ),
        ]
    )
    await test_db.commit()

    body = (await client.get("/api/v1/admin/feedback", headers=test_admin_headers)).json()
    edits = [entry for entry in body["review_entries"] if entry["entry_type"] == "edit"]

    assert body["edited_translations"] == 1
    assert len(edits) == 1
    assert edits[0]["user_correction"] == "Latest wording"


@pytest.mark.asyncio
async def test_review_queue_groups_identical_anonymous_signals(
    client, test_db, test_admin_headers, test_user, test_user_two, conversation_factory
):
    """Separate messages with the same correction should form one readable row."""
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    first = await _translation(test_db, conversation.id, test_user_two.id)
    second = await _translation(test_db, conversation.id, test_user_two.id)
    test_db.add_all(
        [
            TranslationEdit(translation_id=first, editor_id=test_user.id, edited_text="Use this wording"),
            TranslationEdit(translation_id=second, editor_id=test_user.id, edited_text="Use this wording"),
        ]
    )
    await test_db.commit()

    body = (await client.get("/api/v1/admin/feedback", headers=test_admin_headers)).json()
    edits = [entry for entry in body["review_entries"] if entry["entry_type"] == "edit"]

    assert body["edited_translations"] == 2
    assert len(edits) == 1
    assert edits[0]["occurrence_count"] == 2


@pytest.mark.asyncio
async def test_an_empty_system_reports_zeroes_rather_than_failing(
    client, test_admin_headers
):
    """The screen is opened on day one, before anybody has voted on anything."""
    body = (
        await client.get("/api/v1/admin/feedback", headers=test_admin_headers)
    ).json()

    assert body["votes"]["total"] == 0
    assert body["edited_translations"] == 0
    assert body["votes"]["up_rate"] == 0.0
    assert body["shared_corrections"] == []
