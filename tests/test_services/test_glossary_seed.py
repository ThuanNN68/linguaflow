"""Tests for the starter glossary file itself.

The same reasoning as `tests/test_eval/test_golden_set.py`: this is hand-edited
data that nothing else validates, and every way it can be wrong is quiet. A
duplicate scope fails at seed time with a constraint violation somebody has to
decode; a `keep_verbatim` row whose target differs from its source tells the
prompt two contradictory things at once; an unsupported language produces
entries no lookup will ever match.

Nothing here needs a database, which is the point — this checks the file, not
the loader.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.development.seed_glossary import DEFAULT_FILE, REQUIRED_KEYS
from src.schemas.auth import SUPPORTED_LANGUAGES
from src.services.language.glossary import normalize_term

SEED_FILE = Path(__file__).resolve().parents[2] / DEFAULT_FILE


def entries() -> list[dict]:
    """Every row of the shipped seed file."""
    lines = SEED_FILE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_the_seed_file_is_present_and_not_empty():
    """A missing file would make `make seed-glossary` a no-op that reports
    success, and the demo would show a glossary that does nothing."""
    assert SEED_FILE.exists()
    assert len(entries()) > 50


def test_every_entry_carries_every_field_the_loader_needs():
    for index, entry in enumerate(entries(), 1):
        missing = REQUIRED_KEYS - set(entry)
        assert not missing, f"line {index} is missing {sorted(missing)}"


def test_every_language_is_one_the_product_serves():
    """An entry in a language nothing translates into can never match, and
    nothing would ever report that it did not."""
    for entry in entries():
        assert entry["source_language"] in SUPPORTED_LANGUAGES
        assert entry["target_language"] in SUPPORTED_LANGUAGES


def test_no_two_entries_claim_the_same_scope():
    """The database is unique on exactly this key. A duplicate here fails at
    seed time with a constraint violation that says nothing about which line
    caused it."""
    seen: dict[tuple[str, str, str, str, str], int] = {}
    for index, entry in enumerate(entries(), 1):
        key = (
            normalize_term(entry["source_term"]),
            entry["source_language"],
            entry["target_language"],
            entry["domain"],
            entry["audience"],
        )
        assert key not in seen, f"line {index} repeats the scope of line {seen[key]}"
        seen[key] = index


def test_a_verbatim_entry_asks_for_the_term_it_already_has():
    """`keep_verbatim` renders as "leave this untranslated". Pairing it with a
    different target term tells the model two contradictory things in one line."""
    for entry in entries():
        if entry["keep_verbatim"]:
            assert entry["target_term"] == entry["source_term"], entry["source_term"]


@pytest.mark.parametrize("audience", ["internal", "client"])
def test_the_example_the_feature_exists_for_is_actually_in_the_file(audience):
    """UI stays UI for a team and becomes giao dien for a client. If the shipped
    data does not contain it, the demonstration has nothing to demonstrate."""
    matching = [
        entry
        for entry in entries()
        if normalize_term(entry["source_term"]) == "ui"
        and entry["audience"] == audience
    ]

    assert len(matching) == 1
    if audience == "internal":
        assert matching[0]["keep_verbatim"] is True
    else:
        assert matching[0]["target_term"] != "UI"


def test_terms_are_stored_without_stray_whitespace():
    """A term with a trailing space normalises to the same key as one without,
    so the pair would collide in the database while looking distinct here."""
    for entry in entries():
        assert entry["source_term"] == entry["source_term"].strip()
        assert entry["target_term"] == entry["target_term"].strip()
