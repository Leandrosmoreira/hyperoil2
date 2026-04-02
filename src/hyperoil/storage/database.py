"""Async SQLAlchemy database engine and session management."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from hyperoil.observability.logger import get_logger
from hyperoil.storage.models import Base

log = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(sqlite_path: str) -> AsyncEngine:
    """Initialize the async database engine and create tables."""
    global _engine, _session_factory

    # Ensure directory exists
    db_path = Path(sqlite_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite+aiosqlite:///{sqlite_path}"
    _engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args={"timeout": 30},
    )

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    log.info("database_initialized", path=sqlite_path)
    return _engine


def get_session() -> AsyncSession:
    """Get a new async database session."""
    if _session_factory is None:
        msg = "Database not initialized. Call init_db() first."
        raise RuntimeError(msg)
    return _session_factory()


async def close_db() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        log.info("database_closed")
