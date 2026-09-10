"""Alembic environment, wired to the application's own settings.

The connection string is never read from `alembic.ini`: it comes from
`Settings.database_url`, the same value the application connects with, so a
migration can never be applied to a different database than the one running.
That also means the `postgres://` rewriting in `src/config.py` applies here too.
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Alembic runs this file as a script, not as part of the `src` package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import get_settings  # noqa: E402
from src.database.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Everything `--autogenerate` compares the database against.
target_metadata = Base.metadata


def _configure(**kwargs) -> None:
    """Apply the options both the offline and online paths need.

    `render_as_batch` used to matter: it is what let a migration alter a column
    on SQLite, which has no `ALTER COLUMN`, by rebuilding the table instead.
    Since ADR-22 every environment runs PostgreSQL — development and the test
    suite included — so batch mode is now inert, and it is left switched on only
    because turning it off would rewrite migrations for no gain. Do not read it
    as a sign that SQLite is still supported: the schema declares `vector`
    columns, which SQLite cannot create at all.
    """
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Emit SQL for the configured URL without connecting to anything."""
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the migrations on an open, synchronous-facing connection."""
    _configure(connection=connection)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open the async engine and hand a connection to Alembic."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
