"""Test fixtures and configuration."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

os.environ["EMAIL_PROVIDER"] = "memory"

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import get_settings


def _derive_test_database_url() -> str:
    """Name the one database this suite is allowed to destroy.

    Derived from the configured URL rather than hardcoded, so CI — which passes
    its own service-container URL — needs no second variable. The `_test` suffix
    carries more weight than it looks: every test drops a schema on the way out,
    so aiming this at the development database would delete a developer's data
    between one test and the next.
    """
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return override
    url = make_url(get_settings().database_url)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError(
            "The test suite requires PostgreSQL with pgvector (ADR-22), but "
            f"DATABASE_URL names the {url.drivername!r} driver. Start the "
            "database with `docker compose up -d postgres` and point DATABASE_URL at it."
        )
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


TEST_DATABASE_URL = _derive_test_database_url()

# Anything that builds an engine from settings rather than from an injected
# factory — a background translation task handed no `session_factory`, say —
# has to land in the test database as well, never in the developer's. Set
# before the cache is cleared, so the first real read of Settings sees it.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

get_settings.cache_clear()

from src import database as database_module
from src.api.routes import router as api_router
from src.api.websocket import get_connection_manager
from src.api.websocket import router as websocket_router
from src.core.circuit_breaker import (
    embedding_breaker,
    embedding_fallback_breaker,
    fallback_translator_breaker,
    llm_breaker,
)
from src.core.rate_limit import reset_rate_limits
from src.core.security import create_access_token, get_password_hash
from src.database import get_db
from src.database.models import Base, Conversation, ConversationMember, User
from src.services.assistant import assistant_indexing as assistant_indexing_module
from src.services.intelligence import profile_inference as profile_inference_module
from src.services.language import correction_log as correction_log_module
from src.services.language import translation as translation_module
from src.services.messaging import message_memory as message_memory_module
from src.services.messaging.connection_manager import ConnectionManager
from src.services.voice import voice_transcription as voice_transcription_module


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Classify tests by infrastructure dependency from their fixture graph.

    A test that reaches ``test_db`` indirectly through ``client`` or
    ``ws_client`` is still a PostgreSQL integration test. Pytest has already
    expanded that fixture closure in ``item.fixturenames``, so this remains
    accurate when fixtures are refactored and avoids maintaining a second list
    of test files by hand.
    """
    for item in items:
        path_parts = set(item.path.parts)
        needs_postgres = (
            "test_db" in item.fixturenames
            or "_db_for_ws_fixture" in item.fixturenames
            or "test_database" in path_parts
        )
        item.add_marker(pytest.mark.postgres if needs_postgres else pytest.mark.isolated)


@pytest.fixture(autouse=True)
def reset_process_local_resilience_state():
    """Keep singleton counters and breakers from leaking between tests."""
    reset_rate_limits()
    for breaker in (llm_breaker, embedding_breaker, embedding_fallback_breaker, fallback_translator_breaker):
        breaker.reset()
    yield
    reset_rate_limits()
    for breaker in (llm_breaker, embedding_breaker, embedding_fallback_breaker, fallback_translator_breaker):
        breaker.reset()


# ========================
# Test Database Setup
# ========================

# These are initialized in the test_db fixture, after the schema is created.
# Exported for tests that need to verify DB state with a separate session.
# Use these names consistently throughout.
test_engine: create_async_engine | None = None
test_async_session_maker: async_sessionmaker | None = None

_ws_engine: create_async_engine | None = None
_ws_session_maker: async_sessionmaker | None = None

# `CREATE DATABASE` and `CREATE EXTENSION` are needed once for the whole run,
# not once per test, and both are slow enough to be worth not repeating.
_database_prepared = False


async def _ensure_test_database() -> None:
    """Create the test database and its `vector` extension, once per run.

    The extension belongs to the database, not to a schema, so it is installed
    here rather than alongside the per-test tables. It has to exist before any
    `create_all`: a `vector` column on a database without the extension fails at
    CREATE TABLE with "type vector does not exist", which reads like a typo in
    the model rather than a missing extension.

    Alembic also creates the extension (ADR-22), but the suite never runs
    migrations — `Base.metadata.create_all` tests the current models, not the
    migration history — so the two paths each have to stand on their own.
    """
    global _database_prepared
    if _database_prepared:
        return

    url = make_url(TEST_DATABASE_URL)
    maintenance = create_async_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",  # CREATE DATABASE cannot run in a transaction
    )
    try:
        async with maintenance.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        await maintenance.dispose()

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            # Trigram search backs the lexical half of the assistant's hybrid
            # retrieval. Created here as well as in the migration because a test
            # database is built from the models, not by running migrations.
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    finally:
        await engine.dispose()

    _database_prepared = True


async def _run_on_test_database(
    statement: str,
    *,
    ignore_lock_timeout: bool = False,
) -> bool:
    """Execute one DDL statement against the test database, outside a schema.

    A `lock_timeout` is set first, and it is the difference between a slow suite
    and one that never finishes. `_settle_background_translations` cancels the
    tasks it can reach, but a task scheduled from a WebSocket handler runs on
    another event loop and cannot be awaited from here — so it may still be
    inside a read when the schema is dropped, holding a lock this statement then
    waits on. Postgres waits forever by default, and the symptom is a pytest
    process alive with no CPU and no output: twice in one session that cost
    around forty minutes each before anyone looked at `pg_stat_activity`.

    Failing the drop instead leaks one schema into the test database, which is
    cosmetic and clears with `make reset-db`. The suite's job is to report pass
    or fail, not to be a database janitor.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET lock_timeout = '15s'"))
            try:
                await conn.execute(text(statement))
            except DBAPIError as error:
                if not ignore_lock_timeout or getattr(error.orig, "sqlstate", None) != "55P03":
                    raise
                logging.getLogger(__name__).warning(
                    "Timed out on: %s — a background task is still holding a lock. "
                    "Retrying schema cleanup.",
                    statement,
                )
                return False
            return True
    finally:
        await engine.dispose()


async def _drop_test_schema(schema: str) -> None:
    """Drop a per-test schema without making a transient background lock fatal."""
    statement = f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'
    for attempt in range(3):
        if await _run_on_test_database(statement, ignore_lock_timeout=True):
            return
        await asyncio.sleep(0.5 * (attempt + 1))

    logging.getLogger(__name__).warning(
        "Could not drop %s after retries; `make reset-db` clears the leftover schema.",
        schema,
    )


def _init_engines(schema: str) -> None:
    """Point the fixture engine and the WebSocket engine at one private schema.

    Isolation is per schema rather than per database because `CREATE DATABASE`
    costs roughly a second each and the suite has hundreds of tests, while
    `CREATE SCHEMA` is close to free. Both engines share the schema so a row
    written through a fixture is visible to the WebSocket handler, which is the
    same guarantee the previous SQLite file gave.

    `search_path` also lists `public`, where the `vector` extension installs its
    type — without it every embedding column fails to resolve.

    NullPool on purpose, and it is not a performance detail: `ws_client` drives
    the app through starlette's TestClient, which runs it on its own event loop
    in another thread. An asyncpg connection belongs to the loop that opened it,
    so a pooled connection handed across that boundary — or disposed from the
    other side at teardown — fails in ways that surface as unrelated tests going
    red. Holding no connections between checkouts removes the boundary entirely.
    """
    global test_engine, test_async_session_maker, _ws_engine, _ws_session_maker

    connect_args = {"server_settings": {"search_path": f"{schema},public"}}

    # Engine for test fixtures (creating users, conversations, etc.)
    test_engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=NullPool,
        connect_args=connect_args,
    )
    test_async_session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    # Separate engine for WebSocket handler.
    _ws_engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=NullPool,
        connect_args=connect_args,
    )
    _ws_session_maker = async_sessionmaker(
        _ws_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    # And point the application's own engine at this schema as well.
    #
    # `get_db` is overridden by fixtures, so requests were already covered. What
    # was not: a task scheduled from *inside* a request — translating a message,
    # inferring a profile, recording a correction — takes
    # `get_async_session_maker()` rather than an injected factory, because in
    # production there is nothing to inject. That maker knew the test database
    # but not this schema, so the write landed nowhere and the failure was
    # swallowed exactly as a production failure would be: the endpoint returns
    # 201 and the table stays empty.
    #
    # It stayed hidden because every test with a background task either injected
    # a factory or patched the scheduler away — that is, none of them went
    # through the path production uses. Setting the cached engine here fixes the
    # class of problem once, rather than each test file discovering it again.
    database_module._engine = test_engine


async def _settle_background_translations() -> None:
    """Stop background tasks still running for the test that just finished.

    Sending or editing a message schedules translation in a task that
    deliberately outlives the request (`schedule_translations`). No test waits
    for it, so without this the task is still querying when the schema is
    dropped underneath it: the connection is gone, the task dies mid-statement,
    and its session is never checked back in.
    SQLAlchemy then warns from inside the garbage collector, and pytest blames
    whichever test happens to be running at that moment — which is how a change
    in one module could redden a test in another that never touched it.

    Must run *before* the engines are disposed, which is why it is called from
    the fixtures that dispose them rather than from an autouse fixture: an
    autouse fixture is set up first and so torn down last, exactly too late.

    Cancelled rather than awaited: a task blocked on a real LLM call would hang
    the suite. Production never does this — the process is not torn down between
    messages — so this belongs to the harness, not to the service.
    """
    tracked = (
        *translation_module._BACKGROUND_TASKS,
        # Sending a message also schedules a conversation-profile inference,
        # which outlives the request the same way and has the same problem.
        *profile_inference_module._BACKGROUND_TASKS,
        *message_memory_module._BACKGROUND_TASKS,
        # Sending a message also schedules chunk indexing for the assistant's
        # retrieval, which outlives the request the same way (ADR-37).
        *assistant_indexing_module._BACKGROUND_TASKS,
        *correction_log_module._BACKGROUND_TASKS,
        *voice_transcription_module._BACKGROUND_TASKS,
    )
    pending = [task for task in tracked if not task.done()]
    for task in pending:
        task.cancel()

    # Only await the ones this loop owns. `ws_client` drives the app through
    # starlette's TestClient, which runs it on its own loop in another thread,
    # so a task scheduled from a WebSocket handler belongs to that loop —
    # awaiting it from here fails with "attached to a different loop" and turns
    # a passing test into a teardown error. Cancelling is still worth doing for
    # those: it stops them before the schema goes away.
    loop = asyncio.get_running_loop()
    ours = [task for task in pending if task.get_loop() is loop]
    if ours:
        await asyncio.gather(*ours, return_exceptions=True)


async def _close_engines() -> None:
    """Dispose all engines after test."""
    global test_engine, test_async_session_maker, _ws_engine, _ws_session_maker

    if test_engine is not None:
        await test_engine.dispose()
        test_engine = None
        test_async_session_maker = None

    if _ws_engine is not None:
        await _ws_engine.dispose()
        _ws_engine = None
        _ws_session_maker = None

    # Leaving this set would hand the next test an engine bound to a schema that
    # no longer exists — and, once again, silently.
    database_module._engine = None


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_db() -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh schema for each test function.

    Creates all tables at start, then yields a session for test data setup.
    After the test, drops the whole schema — which is one statement instead of
    a table-by-table teardown, and cannot leave an orphan behind if a model is
    added without anybody remembering this fixture.
    """
    global test_engine, test_async_session_maker, _ws_engine, _ws_session_maker

    await _ensure_test_database()

    # A name no other test can collide with, short enough to stay under
    # PostgreSQL's 63-character identifier limit with room to spare.
    schema = f"test_{uuid.uuid4().hex[:12]}"
    await _run_on_test_database(f'CREATE SCHEMA "{schema}"')
    _init_engines(schema)

    # Create all tables.
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Provide a session for test data setup.
    session = test_async_session_maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

    # Drop the schema and clean up. Background translations first: dropping
    # tables under a running task is what strands its connection.
    await _settle_background_translations()
    await _close_engines()
    await _drop_test_schema(schema)


@pytest_asyncio.fixture
async def test_user(test_db: AsyncSession) -> User:
    """Create a test member user."""
    user = User(
        email="test@example.com",
        password_hash=get_password_hash("testpassword"),
        role="member",
        preferred_language="en",
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_admin(test_db: AsyncSession) -> User:
    """Create a test admin user."""
    user = User(
        email="admin@example.com",
        password_hash=get_password_hash("adminpassword"),
        role="admin",
        preferred_language="vi",
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_user_two(test_db: AsyncSession) -> User:
    """Create a second member used for direct-chat delivery tests."""
    user = User(
        email="second@example.com",
        password_hash=get_password_hash("secondpassword"),
        role="member",
        preferred_language="vi",
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_user_three(test_db: AsyncSession) -> User:
    """Create a third member used for group-chat delivery tests."""
    user = User(
        email="third@example.com",
        password_hash=get_password_hash("thirdpassword"),
        role="member",
        preferred_language="ja",
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest_asyncio.fixture
async def conversation_factory(test_db: AsyncSession):
    """Create a durable test conversation with the supplied members."""

    async def create(
        creator: User,
        members: list[User],
        conversation_type: str = "direct",
        title: str | None = None,
    ) -> Conversation:
        unique_member_ids = list(dict.fromkeys([*(member.id for member in members), creator.id]))
        conversation = Conversation(
            type=conversation_type,
            title=title,
            created_by=creator.id,
        )
        test_db.add(conversation)
        await test_db.flush()
        test_db.add_all(
            [
                ConversationMember(
                    conversation_id=conversation.id,
                    user_id=user_id,
                )
                for user_id in unique_member_ids
            ]
        )
        await test_db.commit()
        await test_db.refresh(conversation)
        return conversation

    return create


def auth_headers_for_user(user: User) -> dict[str, str]:
    """Generate authorization headers for a user."""
    token = create_access_token(subject=user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_user_headers(test_user: User) -> dict[str, str]:
    """Authorization headers for test_user."""
    return auth_headers_for_user(test_user)


@pytest.fixture
def test_admin_headers(test_admin: User) -> dict[str, str]:
    """Authorization headers for test_admin."""
    return auth_headers_for_user(test_admin)


# ========================
# App Client Setup
# ========================


@pytest_asyncio.fixture
async def client(
    test_db: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for testing API endpoints.

    Depends on test_db to ensure the database is initialized before use.
    """
    from src.main import app

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        # Use the same session maker as test_db for consistency
        if test_async_session_maker is None:
            raise RuntimeError("test_db fixture must be used before client fixture")
        session = test_async_session_maker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def ws_client(
    _db_for_ws_fixture: None,
) -> AsyncGenerator[tuple[TestClient, ConnectionManager], None]:
    """Provide a no-lifespan WebSocket app backed by the test database.

    This fixture depends on _db_for_ws_fixture which initializes the DB if test_db
    hasn't been used.
    """
    if _ws_session_maker is None:
        raise RuntimeError("Database not initialized. Use _db_for_ws or test_db fixture.")

    ws_app = FastAPI()
    ws_app.include_router(api_router, prefix="/api/v1")
    ws_app.include_router(websocket_router, prefix="/api/v1")
    manager = ConnectionManager()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        session = _ws_session_maker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    ws_app.dependency_overrides[get_db] = override_get_db
    ws_app.dependency_overrides[get_connection_manager] = lambda: manager

    with TestClient(ws_app) as test_client:
        yield test_client, manager

    ws_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def _db_for_ws_fixture() -> None:
    """Initialize database for ws_client when test_db is not used.

    This is a no-op when test_db is already initialized.
    """
    global test_engine
    if test_engine is None:
        await _ensure_test_database()
        schema = f"test_{uuid.uuid4().hex[:12]}"
        await _run_on_test_database(f'CREATE SCHEMA "{schema}"')
        _init_engines(schema)

        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield

        await _settle_background_translations()
        await _close_engines()
        await _drop_test_schema(schema)
    else:
        yield


@pytest.fixture
def mock_llm() -> AsyncMock:
    """Mock LLM to avoid calling OpenAI during tests."""
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock
