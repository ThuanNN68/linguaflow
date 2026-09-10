"""Tests for the glossary review queue.

Two things are being guarded. The gate, because `users.role` compared to
`"admin"` is the entire authorization model and a missing dependency looks like
working code. And what a proposal is allowed to carry: an administrator is
barred from reading conversation content, and the only thing standing between
them and it is which columns these responses expose.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    GlossaryEntry,
    GlossaryProposal,
    GlossaryProposalCitation,
)
from src.services.language.glossary import normalize_term


@pytest_asyncio.fixture
async def proposal(test_db: AsyncSession) -> GlossaryProposal:
    """One pending proposal with the evidence a reviewer judges on."""
    row = GlossaryProposal(
        source_term="user interface",
        source_term_normalized=normalize_term("user interface"),
        target_term="giao dien",
        source_language="en",
        target_language="vi",
        domain="software delivery",
        audience="an external client",
        keep_verbatim=False,
        status="pending",
        occurrence_count=4,
        distinct_user_count=3,
        rationale="Three people replaced the literal rendering with this one.",
    )
    test_db.add(row)
    await test_db.flush()
    test_db.add(
        GlossaryProposalCitation(
            proposal_id=row.id,
            anonymized_snippet="… please review the user interface again …",
        )
    )
    await test_db.commit()
    await test_db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_a_member_cannot_read_the_review_queue(
    client, test_db, test_user_headers, proposal
):
    """The gate is one string comparison and no middleware backs it up, so every
    endpoint needs its own test that it is actually there."""
    response = await client.get(
        "/api/v1/admin/glossary/proposals", headers=test_user_headers
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_member_cannot_approve_a_proposal(
    client, test_db, test_user_headers, proposal
):
    response = await client.post(
        f"/api/v1/admin/glossary/proposals/{proposal.id}/approve",
        headers=test_user_headers,
        json={},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_queue_shows_counts_and_snippets_and_nothing_else(
    client, test_db, test_admin_headers, proposal
):
    """The privacy boundary in one assertion. A reviewer judges on how many
    people corrected the term and a few anonymised words; anything naming a
    person, a conversation or a message would put them inside content they are
    not allowed to read (docs/architecture/assistant-admin.md, diagram 2)."""
    response = await client.get(
        "/api/v1/admin/glossary/proposals", headers=test_admin_headers
    )

    body = response.json()
    assert len(body) == 1
    row = body[0]
    assert (row["occurrence_count"], row["distinct_user_count"]) == (4, 3)
    assert row["citations"][0]["anonymized_snippet"].startswith("…")
    assert not {"user_id", "message_id", "conversation_id"} & set(row)


@pytest.mark.asyncio
async def test_approving_a_proposal_puts_the_term_into_use(
    client, test_db, test_admin_headers, proposal
):
    response = await client.post(
        f"/api/v1/admin/glossary/proposals/{proposal.id}/approve",
        headers=test_admin_headers,
        json={},
    )

    assert response.status_code == 201
    entry = await test_db.scalar(select(GlossaryEntry))
    assert (entry.source_term, entry.target_term) == ("user interface", "giao dien")
    assert entry.status == "active"


@pytest.mark.asyncio
async def test_an_approval_may_correct_the_term_on_the_way_through(
    client, test_db, test_admin_headers, proposal
):
    """The miner's answer came from a model reading fragments; the administrator
    knows what the team actually says. Making them reject and re-add a
    nearly-right term is how a queue stops being worked."""
    response = await client.post(
        f"/api/v1/admin/glossary/proposals/{proposal.id}/approve",
        headers=test_admin_headers,
        json={"target_term": "giao dien nguoi dung", "audience": "client"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["target_term"] == "giao dien nguoi dung"
    assert body["audience"] == "client"


@pytest.mark.asyncio
async def test_an_approved_proposal_is_kept_rather_than_deleted(
    client, test_db, test_admin_headers, proposal
):
    """It is the record of where the entry came from, and it is what stops the
    miner proposing the same term again next week."""
    await client.post(
        f"/api/v1/admin/glossary/proposals/{proposal.id}/approve",
        headers=test_admin_headers,
        json={},
    )

    stored = await test_db.scalar(
        select(GlossaryProposal)
        .where(GlossaryProposal.id == proposal.id)
        .execution_options(populate_existing=True)
    )
    assert stored.status == "approved"
    assert stored.reviewed_at is not None


@pytest.mark.asyncio
async def test_a_rejection_requires_a_reason(
    client, test_db, test_admin_headers, proposal
):
    """The row is kept forever and compared against future candidates, so months
    later somebody will want to know why a sensible term never made it in."""
    response = await client.post(
        f"/api/v1/admin/glossary/proposals/{proposal.id}/reject",
        headers=test_admin_headers,
        json={"reason": "   "},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_rejected_proposal_keeps_its_reason_and_its_evidence(
    client, test_db, test_admin_headers, proposal
):
    response = await client.post(
        f"/api/v1/admin/glossary/proposals/{proposal.id}/reject",
        headers=test_admin_headers,
        json={"reason": "We say UI internally and this entry would override that."},
    )

    body = response.json()
    assert body["status"] == "rejected"
    assert "UI internally" in body["reject_reason"]
    assert len(body["citations"]) == 1


@pytest.mark.asyncio
async def test_deciding_a_proposal_twice_is_refused(
    client, test_db, test_admin_headers, proposal
):
    """Two reviewers working the queue at once would otherwise both believe they
    were the one who decided it, and the second would overwrite the first —
    including its reason."""
    await client.post(
        f"/api/v1/admin/glossary/proposals/{proposal.id}/reject",
        headers=test_admin_headers,
        json={"reason": "not a term"},
    )

    second = await client.post(
        f"/api/v1/admin/glossary/proposals/{proposal.id}/approve",
        headers=test_admin_headers,
        json={},
    )

    assert second.status_code == 409


@pytest.mark.asyncio
async def test_an_administrator_can_add_a_term_without_waiting_for_corrections(
    client, test_db, test_admin_headers
):
    """Mining needs several people to disagree first, which is right for
    discovering a house style and useless for a term the team already knows."""
    response = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={
            "source_term": "staging environment",
            "target_term": "moi truong staging",
            "source_language": "en",
            "target_language": "vi",
        },
    )

    assert response.status_code == 201
    assert response.json()["source_term"] == "staging environment"


@pytest.mark.asyncio
async def test_the_same_term_and_scope_cannot_be_added_twice(
    client, test_db, test_admin_headers
):
    """Two renderings for one scope would make the lookup pick arbitrarily."""
    payload = {
        "source_term": "deploy",
        "target_term": "trien khai",
        "source_language": "en",
        "target_language": "vi",
    }
    await client.post("/api/v1/admin/glossary", headers=test_admin_headers, json=payload)

    second = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={**payload, "target_term": "deploy"},
    )

    assert second.status_code == 409


@pytest.mark.asyncio
async def test_a_language_the_product_does_not_serve_is_refused(
    client, test_admin_headers
):
    response = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={
            "source_term": "deploy",
            "target_term": "x",
            "source_language": "en",
            "target_language": "zz",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_retiring_an_entry_takes_it_out_of_use_without_deleting_it(
    client, test_db, test_admin_headers
):
    """A translation delivered last month was shaped by this term; removing the
    row would erase the only explanation for the wording a reader is seeing."""
    created = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={
            "source_term": "hotfix",
            "target_term": "ban va gap",
            "source_language": "en",
            "target_language": "vi",
        },
    )
    entry_id = created.json()["id"]

    response = await client.delete(
        f"/api/v1/admin/glossary/{entry_id}", headers=test_admin_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "retired"

    listed = await client.get("/api/v1/admin/glossary", headers=test_admin_headers)
    assert listed.json() == []

    including = await client.get(
        "/api/v1/admin/glossary?include_retired=true", headers=test_admin_headers
    )
    assert len(including.json()) == 1


@pytest.mark.asyncio
async def test_a_member_cannot_edit_an_entry(client, test_db, test_user_headers):
    """The gate is one string comparison per endpoint, so each new one needs its own."""
    response = await client.patch(
        "/api/v1/admin/glossary/does-not-matter",
        headers=test_user_headers,
        json={"target_term": "x"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_editing_an_entry_changes_only_the_fields_that_were_sent(
    client, test_db, test_admin_headers
):
    """A screen that knows nothing about a column added later must not blank it."""
    created = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={
            "source_term": "rollback",
            "target_term": "quay lui",
            "source_language": "en",
            "target_language": "vi",
            "audience": "client",
        },
    )
    entry_id = created.json()["id"]

    response = await client.patch(
        f"/api/v1/admin/glossary/{entry_id}",
        headers=test_admin_headers,
        json={"target_term": "khoi phuc ban truoc"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target_term"] == "khoi phuc ban truoc"
    assert body["source_term"] == "rollback"
    assert body["audience"] == "client"


@pytest.mark.asyncio
async def test_editing_the_source_term_renormalizes_it_for_the_lookup(
    client, test_db, test_admin_headers
):
    """The lookup matches on the normalised column, so an edit that left it
    behind would fix the display and leave the machine matching the old word."""
    created = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={
            "source_term": "staging enviroment",
            "target_term": "moi truong staging",
            "source_language": "en",
            "target_language": "vi",
        },
    )
    entry_id = created.json()["id"]

    await client.patch(
        f"/api/v1/admin/glossary/{entry_id}",
        headers=test_admin_headers,
        json={"source_term": "Staging Environment"},
    )

    entry = await test_db.scalar(
        select(GlossaryEntry).where(GlossaryEntry.id == entry_id)
    )
    await test_db.refresh(entry)
    assert entry.source_term == "Staging Environment"
    assert entry.source_term_normalized == "staging environment"


@pytest.mark.asyncio
async def test_editing_an_entry_onto_a_taken_scope_is_refused(
    client, test_db, test_admin_headers
):
    """An edit collides with the unique constraint exactly as an insert does."""
    common = {"source_language": "en", "target_language": "vi", "audience": "client"}
    await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={"source_term": "release", "target_term": "phat hanh", **common},
    )
    second = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={"source_term": "shipment", "target_term": "chuyen hang", **common},
    )

    response = await client.patch(
        f"/api/v1/admin/glossary/{second.json()['id']}",
        headers=test_admin_headers,
        json={"source_term": "release"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_editing_an_entry_that_is_not_there_is_a_404(client, test_admin_headers):
    response = await client.patch(
        "/api/v1/admin/glossary/00000000-0000-0000-0000-000000000000",
        headers=test_admin_headers,
        json={"target_term": "x"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_restoring_a_retired_entry_puts_it_back_into_use(
    client, test_db, test_admin_headers
):
    """Nothing was deleted, so restoring keeps the id and the approval date
    rather than creating a second row a reader would have to compare."""
    created = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={
            "source_term": "changelog",
            "target_term": "nhat ky thay doi",
            "source_language": "en",
            "target_language": "vi",
        },
    )
    entry_id = created.json()["id"]
    await client.delete(f"/api/v1/admin/glossary/{entry_id}", headers=test_admin_headers)

    response = await client.post(
        f"/api/v1/admin/glossary/{entry_id}/restore", headers=test_admin_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["id"] == entry_id

    listed = await client.get("/api/v1/admin/glossary", headers=test_admin_headers)
    assert [row["id"] for row in listed.json()] == [entry_id]


@pytest.mark.asyncio
async def test_a_member_cannot_restore_an_entry(client, test_db, test_user_headers):
    response = await client.post(
        "/api/v1/admin/glossary/does-not-matter/restore", headers=test_user_headers
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_member_cannot_delete_an_entry_permanently(client, test_user_headers):
    response = await client.delete(
        "/api/v1/admin/glossary/does-not-matter/permanent", headers=test_user_headers
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_an_active_entry_cannot_be_deleted_permanently(
    client, test_db, test_admin_headers
):
    """Retirement stays the way a term in use is stopped. An entry that is
    active has shaped translations people may still be reading, so the answer
    to "delete this" is "retire it first and see"."""
    created = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={
            "source_term": "canary",
            "target_term": "ban thu nghiem",
            "source_language": "en",
            "target_language": "vi",
        },
    )

    response = await client.delete(
        f"/api/v1/admin/glossary/{created.json()['id']}/permanent",
        headers=test_admin_headers,
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_deleting_a_retired_entry_permanently_removes_the_row(
    client, test_db, test_admin_headers
):
    """The case retirement does not cover: a term typed in by mistake explains
    nothing, because it never shaped anything anybody saw."""
    created = await client.post(
        "/api/v1/admin/glossary",
        headers=test_admin_headers,
        json={
            "source_term": "tpyo",
            "target_term": "loi go",
            "source_language": "en",
            "target_language": "vi",
        },
    )
    entry_id = created.json()["id"]
    await client.delete(f"/api/v1/admin/glossary/{entry_id}", headers=test_admin_headers)

    response = await client.delete(
        f"/api/v1/admin/glossary/{entry_id}/permanent", headers=test_admin_headers
    )

    assert response.status_code == 200
    assert response.json()["source_term"] == "tpyo"

    gone = await test_db.scalar(
        select(GlossaryEntry).where(GlossaryEntry.id == entry_id)
    )
    assert gone is None

    listed = await client.get(
        "/api/v1/admin/glossary?include_retired=true", headers=test_admin_headers
    )
    assert listed.json() == []


@pytest.mark.asyncio
async def test_the_same_term_can_be_added_again_after_a_permanent_delete(
    client, test_db, test_admin_headers
):
    """The point of deleting rather than retiring: the scope is free again.
    A retired row still holds the unique constraint, so re-adding the corrected
    term would answer 409 without this."""
    payload = {
        "source_term": "rollout",
        "target_term": "trien khai dan",
        "source_language": "en",
        "target_language": "vi",
    }
    first = await client.post(
        "/api/v1/admin/glossary", headers=test_admin_headers, json=payload
    )
    entry_id = first.json()["id"]
    await client.delete(f"/api/v1/admin/glossary/{entry_id}", headers=test_admin_headers)
    await client.delete(
        f"/api/v1/admin/glossary/{entry_id}/permanent", headers=test_admin_headers
    )

    again = await client.post(
        "/api/v1/admin/glossary", headers=test_admin_headers, json=payload
    )

    assert again.status_code == 201
