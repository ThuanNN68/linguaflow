"""Tests that consent, and only consent, opens the correction to mining.

ADR-19 made `translation_edits` private to its author. The glossary is mined
from a separate, narrower record, and `consent_to_share` is the single thing
that makes writing that record legitimate rather than an exception to the rule.
A regression here would not fail anything: corrections would simply start being
collected from people who declined, and the only symptom would be terms nobody
can explain appearing in a review queue.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import CorrectionLog, Message, TranslationResult


@pytest_asyncio.fixture
async def translation(
    test_db: AsyncSession, test_user, test_user_two, conversation_factory
) -> TranslationResult:
    """One stored translation the calling account is entitled to correct."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="group"
    )
    message = Message(
        client_message_id=f"m-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="Please review the user interface",
        source_language="en",
    )
    test_db.add(message)
    await test_db.flush()

    row = TranslationResult(
        message_id=message.id,
        target_language="en",
        honorific_profile="peer",
        translated_text="Please review the user interface again",
        model="test-model",
        latency_ms=1,
        is_fallback=False,
    )
    test_db.add(row)
    await test_db.commit()
    await test_db.refresh(row)
    return row


async def settle() -> None:
    """Let the detached recording task finish before reading the table."""
    from src.services.language import correction_log

    pending = [task for task in correction_log._BACKGROUND_TASKS if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def corrections(test_db: AsyncSession) -> list[CorrectionLog]:
    """Everything recorded for mining so far."""
    return list((await test_db.scalars(select(CorrectionLog))).all())


@pytest.mark.asyncio
async def test_an_edit_without_consent_leaves_nothing_to_mine(
    client, test_db, test_user_headers, translation
):
    """The default. Nobody has to opt out of anything."""
    response = await client.post(
        f"/api/v1/translations/{translation.id}/edits",
        headers=test_user_headers,
        json={"edited_text": "Please review the UI again"},
    )
    assert response.status_code == 201
    await settle()

    assert await corrections(test_db) == []


@pytest.mark.asyncio
async def test_consent_records_the_term_the_reader_replaced(
    client, test_db, test_user, test_user_headers, translation
):
    """What gets stored is the pair and a redacted snippet — not the edit."""
    response = await client.post(
        f"/api/v1/translations/{translation.id}/edits",
        headers=test_user_headers,
        json={"edited_text": "Please review the UI again", "consent_to_share": True},
    )
    assert response.status_code == 201
    await settle()

    rows = await corrections(test_db)
    assert len(rows) == 1
    assert (rows[0].source_phrase, rows[0].corrected_target) == ("user interface", "UI")
    assert rows[0].consent_to_share is True
    assert rows[0].user_id == test_user.id
    assert "user interface" in rows[0].anonymized_snippet
    # `original_snippet` previews the sender's own wording — the fixture's
    # `Message.original_text` — not the reader's edit or the machine's
    # rendering, which is what `anonymized_snippet` already covers.
    assert "Please review the user interface" in rows[0].original_snippet


@pytest.mark.asyncio
async def test_consenting_to_a_rewrite_still_records_nothing(
    client, test_db, test_user_headers, translation
):
    """Consent opens the door; it does not make every edit a term. Most edits
    are somebody rephrasing a sentence, and proposing those would fill the
    review queue with things nobody can act on."""
    response = await client.post(
        f"/api/v1/translations/{translation.id}/edits",
        headers=test_user_headers,
        json={
            "edited_text": "Kindly take another look at the UI before Friday",
            "consent_to_share": True,
        },
    )
    assert response.status_code == 201
    await settle()

    assert await corrections(test_db) == []


@pytest.mark.asyncio
async def test_the_edit_response_is_unchanged_by_consent(
    client, test_user_headers, translation
):
    """Recording is a by-product. Nothing about mining a glossary may change
    what the reader's own correction does or returns."""
    response = await client.post(
        f"/api/v1/translations/{translation.id}/edits",
        headers=test_user_headers,
        json={"edited_text": "Please review the UI again", "consent_to_share": True},
    )

    body = response.json()
    assert body["edited_text"] == "Please review the UI again"
    assert body["translation_id"] == translation.id
    assert "consent_to_share" not in body
