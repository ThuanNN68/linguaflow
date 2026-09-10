"""Database module for async SQLAlchemy setup."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings

# Lazy load settings to avoid import errors during testing
# when JWT_SECRET might not be set
_settings = None


def _get_settings():
    """Get settings with caching."""
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


def _get_engine():
    """Get or create async engine."""
    settings = _get_settings()
    # SQLite is served by a pool that takes no size — passing one raises — and
    # sizing it would mean nothing anyway for a file opened by one process.
    pool_options = (
        {}
        if settings.database_url.startswith("sqlite")
        else {
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
            "pool_recycle": settings.database_pool_recycle_seconds,
            "pool_timeout": settings.database_pool_timeout_seconds,
            # Prefer recently used connections so the oldest idle connections
            # are recycled first when talking through a managed pooler.
            "pool_use_lifo": True,
        }
    )
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_pre_ping=True,
        **pool_options,
    )


# Cache the engine
_engine = None


def get_engine():
    """Get or create async engine with caching."""
    global _engine
    if _engine is None:
        _engine = _get_engine()
    return _engine


def get_async_session_maker():
    """Get or create async session maker with caching."""
    engine = get_engine()
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session."""
    session_maker = get_async_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
