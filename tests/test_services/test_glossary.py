"""Tests for choosing which glossary terms a message has to honour.

The scope rules are the point. One source term can hold several entries that
differ only in who is reading, and picking the wrong one does not fail — it
produces a fluent translation using the vocabulary meant for somebody else,
which is the exact confusion the glossary exists to remove.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.database.models import EMBEDDING_DIM, GlossaryEntry
from src.services.language.glossary import lookup_terms, normalize_term

VALID_SECRET = "x" * 48


def settings(**overrides) -> Settings:
    """Build settings without the developer's own .env deciding the outcome."""
    return Settings(jwt_secret=VALID_SECRET, **overrides)


def entry(
    source: str,
    target: str,
    *,
    domain: str = "",
    audience: str = "",
    keep_verbatim: bool = False,
    embedding: list[float] | None = None,
) -> GlossaryEntry:
    """An active en→vi entry scoped as asked."""
    return GlossaryEntry(
        source_term=source,
        source_term_normalized=normalize_term(source),
        target_term=target,
        source_language="en",
        target_language="vi",
        domain=domain,
        audience=audience,
        keep_verbatim=keep_verbatim,
        status="active",
        embedding=embedding,
    )


def vector(*leading: float) -> list[float]:
    """Pad a few meaningful components out to the column's fixed width.

    The width is not negotiable — it is EMBEDDING_DIM in the schema — so a
    hand-written three-component vector is rejected by the database rather than
    by any code under test.
    """
    return [*leading] + [0.0] * (EMBEDDING_DIM - len(leading))


@pytest_asyncio.fixture
async def ui_glossary(test_db: AsyncSession) -> None:
    """The feature's own example: UI stays UI internally, giao dien for a client."""
    test_db.add_all(
        [
            entry("UI", "UI", audience="internal", keep_verbatim=True),
            entry("UI", "giao dien", audience="client"),
            entry("deploy", "deploy", keep_verbatim=True),
        ]
    )
    await test_db.commit()


async def terms_for(test_db, text: str, **kwargs) -> dict[str, str]:
    """Run a lookup and reduce it to source-to-target pairs."""
    found = await lookup_terms(
        test_db,
        text=text,
        source_language="en",
        target_language="vi",
        settings=settings(),
        **kwargs,
    )
    return {term.source_term: term.target_term for term in found}


@pytest.mark.asyncio
async def test_the_same_term_resolves_by_who_is_reading(test_db, ui_glossary):
    """The whole feature in one case."""
    internal = await terms_for(test_db, "Please review the UI", audience="internal")
    client = await terms_for(test_db, "Please review the UI", audience="client")

    assert internal == {"UI": "UI"}
    assert client == {"UI": "giao dien"}


@pytest.mark.asyncio
async def test_an_unscoped_entry_applies_when_nothing_narrower_does(
    test_db, ui_glossary
):
    """Empty scope means "everywhere", and it is what an unprofiled conversation
    has to fall back on — which is every conversation at first."""
    assert await terms_for(test_db, "Time to deploy") == {"deploy": "deploy"}


@pytest.mark.asyncio
async def test_a_conversation_with_no_audience_gets_no_scoped_term(
    test_db, ui_glossary
):
    """Both UI entries are scoped, so a conversation nobody has profiled matches
    neither. Guessing one would be worse: it would pick a register at random."""
    assert await terms_for(test_db, "Please review the UI") == {}


@pytest.mark.asyncio
async def test_a_term_inside_a_longer_word_is_not_matched(test_db, ui_glossary):
    """Substring matching would fire "UI" inside "building" and then *force* a
    rendering into a word that has nothing to do with the glossary."""
    assert await terms_for(test_db, "We are building it", audience="client") == {}


@pytest.mark.asyncio
async def test_matching_ignores_case_and_spacing(test_db):
    """People type "Staging  Environment" and mean the entry."""
    test_db.add(entry("staging environment", "moi truong staging", keep_verbatim=False))
    await test_db.commit()

    found = await terms_for(test_db, "Check the Staging   Environment please")

    assert found == {"staging environment": "moi truong staging"}


@pytest.mark.asyncio
async def test_a_narrower_scope_replaces_the_catch_all(test_db):
    """Both apply; only one may be sent, or the model is handed two answers and
    left to choose between them."""
    test_db.add_all(
        [
            entry("release", "ban phat hanh"),
            entry("release", "release", audience="internal", keep_verbatim=True),
        ]
    )
    await test_db.commit()

    found = await terms_for(test_db, "Ship the release", audience="internal")

    assert found == {"release": "release"}


@pytest.mark.asyncio
async def test_a_retired_entry_is_never_sent(test_db):
    """Retired rather than deleted, because a translation delivered last month
    was shaped by it — but it must stop shaping new ones."""
    retired = entry("hotfix", "ban va gap")
    retired.status = "retired"
    test_db.add(retired)
    await test_db.commit()

    assert await terms_for(test_db, "Push the hotfix") == {}


@pytest.mark.asyncio
async def test_the_other_language_pairs_entries_are_not_considered(test_db):
    """An entry is a rendering *into* one language and says nothing about another."""
    other = entry("UI", "Benutzeroberflaeche")
    other.target_language = "de"
    test_db.add(other)
    await test_db.commit()

    assert await terms_for(test_db, "Check the UI") == {}


@pytest.mark.asyncio
async def test_the_flag_decides_whether_a_lookup_embeds_anything(test_db, monkeypatch):
    """The flag has to gate the embedding call itself, not just its result.

    It is what stands between a lookup that costs nothing and one that adds a
    network round trip to the request path, and NFR-01 is the tightest figure in
    the project. Asserted in both directions rather than against the current
    default, so this keeps testing the gate after somebody changes which way the
    default points — as somebody did (ADR-26)."""
    called: list[str] = []

    async def spy(text, *, settings=None):
        called.append(text)
        return None

    monkeypatch.setattr("src.services.language.glossary.embed", spy)
    test_db.add(entry("staging environment", "moi truong staging"))
    await test_db.commit()

    await lookup_terms(
        test_db,
        text="how is stg looking",
        source_language="en",
        target_language="vi",
        settings=settings(semantic_glossary_enabled=False),
    )
    assert called == []

    await lookup_terms(
        test_db,
        text="how is stg looking",
        source_language="en",
        target_language="vi",
        settings=settings(semantic_glossary_enabled=True),
    )
    assert called == ["how is stg looking"]


@pytest.mark.asyncio
async def test_a_wording_the_glossary_never_saw_is_matched_by_meaning(
    test_db, monkeypatch
):
    """The reason for the second stage: people type "stg", not the entry's own
    words, and an exact match will never find them."""
    test_db.add(
        entry("staging environment", "moi truong staging", embedding=vector(1.0))
    )
    await test_db.commit()

    async def near(text, *, settings=None):
        return vector(0.99, 0.14)

    monkeypatch.setattr("src.services.language.glossary.embed", near)

    found = await lookup_terms(
        test_db,
        text="how is stg looking",
        source_language="en",
        target_language="vi",
        settings=settings(semantic_glossary_enabled=True),
    )

    assert [term.target_term for term in found] == ["moi truong staging"]


@pytest.mark.asyncio
async def test_an_unrelated_wording_stays_below_the_threshold(test_db, monkeypatch):
    """The threshold is conservative on purpose: a wrongly matched term is
    *forced* into the translation, which is worse than missing one."""
    test_db.add(
        entry("staging environment", "moi truong staging", embedding=vector(1.0))
    )
    await test_db.commit()

    async def far(text, *, settings=None):
        return vector(0.0, 1.0)

    monkeypatch.setattr("src.services.language.glossary.embed", far)

    found = await lookup_terms(
        test_db,
        text="what is for lunch",
        source_language="en",
        target_language="vi",
        settings=settings(semantic_glossary_enabled=True),
    )

    assert found == ()


@pytest.mark.asyncio
async def test_a_glossary_that_cannot_be_read_costs_terms_and_nothing_else(test_db):
    """The caller is a graph node. A missing glossary costs consistency, never
    delivery (NFR-02)."""
    assert (
        await lookup_terms(
            test_db, text="", source_language="en", target_language="vi"
        )
        == ()
    )
    assert (
        await lookup_terms(
            test_db, text="hello", source_language="", target_language="vi"
        )
        == ()
    )
