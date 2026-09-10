"""Tests for the settings that only a deployment exercises.

Every case here fires at import time in production, where nobody is watching a
test run — a wrong value shows up as a container that either will not start or,
worse, starts with a secret anyone can guess.

`Settings` is built with explicit keyword arguments so the developer's own `.env`
cannot decide the outcome.
"""

import pytest

from src.config import Settings

VALID_SECRET = "x" * 48


def settings(**overrides) -> Settings:
    """Build settings with a usable secret unless the test overrides it."""
    return Settings(jwt_secret=VALID_SECRET, **overrides)


def test_a_plain_postgres_url_is_moved_onto_the_async_driver():
    """Managed databases hand out `postgresql://`, which is the sync driver."""
    resolved = settings(database_url="postgresql://user:pass@host:5432/db")

    assert resolved.database_url == "postgresql+asyncpg://user:pass@host:5432/db"


def test_the_heroku_style_postgres_scheme_is_moved_onto_the_async_driver():
    resolved = settings(database_url="postgres://user:pass@host:5432/db")

    assert resolved.database_url == "postgresql+asyncpg://user:pass@host:5432/db"


def test_a_url_that_already_names_a_driver_is_left_alone():
    """Including one that names a driver this project does not install."""
    resolved = settings(database_url="postgresql+psycopg://user:pass@host/db")

    assert resolved.database_url == "postgresql+psycopg://user:pass@host/db"


def test_the_default_database_url_names_postgres_with_the_async_driver():
    """The default has to work untouched: it is what a fresh checkout runs on.

    Read from the field definition rather than from a constructed `Settings`.
    The suite exports `DATABASE_URL` so that everything lands in its own
    database (`tests/conftest.py`), so a constructed instance would report that
    value and the assertion would pass no matter what the default said.

    PostgreSQL rather than SQLite since ADR-22: the schema declares `vector`
    columns and SQLite has no such type.
    """
    default = Settings.model_fields["database_url"].default

    assert default.startswith("postgresql+asyncpg://")
    # And the rewrite validator leaves an already-async URL alone.
    assert settings(database_url=default).database_url == default


def test_database_sql_echo_is_opt_in_to_protect_bound_message_text():
    assert Settings.model_fields["database_echo"].default is False
    assert settings(database_echo=True).database_echo is True


def test_cors_origins_are_split_and_stripped():
    """A list written the way a human writes one must still match an Origin."""
    resolved = settings(cors_origins="http://localhost:3000, https://app.example.com ")

    # localhost:3000 is already present; auto-append adds localhost:3001 and public_frontend_origin
    assert resolved.cors_origin_list[:2] == ["http://localhost:3000", "https://app.example.com"]
    assert "http://localhost:3001" in resolved.cors_origin_list


def test_an_empty_cors_entry_is_dropped():
    """A trailing comma would otherwise produce an origin that matches nothing."""
    resolved = settings(cors_origins="http://localhost:3000,,")

    # localhost:3000 already present; auto-append adds localhost:3001 and public_frontend_origin
    assert resolved.cors_origin_list[0] == "http://localhost:3000"
    assert "http://localhost:3001" in resolved.cors_origin_list


def test_a_missing_jwt_secret_is_refused_in_every_environment():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(jwt_secret="")


def test_the_example_jwt_secret_is_refused_in_production():
    """It passes an "is it empty" check, which is exactly the danger."""
    with pytest.raises(ValueError, match="example value"):
        Settings(app_env="production", jwt_secret="your-secret-key-here")


def test_a_short_jwt_secret_is_refused_in_production():
    with pytest.raises(ValueError, match="at least"):
        Settings(app_env="production", jwt_secret="short-but-not-a-placeholder")


def test_a_short_jwt_secret_is_allowed_outside_production():
    """Development is not where this rule earns anything, and it costs setup."""
    assert Settings(app_env="development", jwt_secret="dev").jwt_secret == "dev"
