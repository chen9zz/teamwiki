import builtins
import socket
from collections.abc import AsyncIterator

from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, settings


def build_engine(config: Settings) -> AsyncEngine:
    """Bound each process's connections and apply PostgreSQL statement deadlines."""
    url = make_url(config.database_url)
    options = {"pool_pre_ping": True, "pool_recycle": config.db_pool_recycle_seconds}
    # In-memory SQLite uses a static pool and does not accept queue pool options.
    if url.get_backend_name() != "sqlite" or url.database not in {None, "", ":memory:"}:
        options.update(
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_timeout=config.db_pool_timeout_seconds,
        )
    if url.drivername == "postgresql+asyncpg":
        options["connect_args"] = {
            "timeout": config.db_pool_timeout_seconds,
            "server_settings": {
                "application_name": "teamwiki",
                "statement_timeout": str(config.db_statement_timeout_ms),
                "lock_timeout": str(config.db_lock_timeout_ms),
            },
        }
    return create_async_engine(config.database_url, **options)


def capacity_failure_reason(error: Exception) -> str | None:
    """Classify capacity failures only; SQL/programming errors remain server errors."""
    if isinstance(error, TimeoutError):
        return "pool_timeout"
    # asyncpg connection establishment can raise builtins directly, without a
    # SQLAlchemy DBAPI wrapper. Keep file/permission OSErrors out of this branch.
    if isinstance(error, builtins.TimeoutError):
        return "connect_timeout"
    if isinstance(error, (ConnectionError, socket.gaierror)):
        return "unavailable"
    if not isinstance(error, DBAPIError):
        return None
    code = getattr(error.orig, "sqlstate", None)
    if code == "57014":
        return "statement_timeout"
    if code == "55P03":
        return "lock_timeout"
    if code in {"53300", "53400", "57P01", "57P02", "57P03"} or (code and code.startswith("08")):
        return "unavailable"
    return None


engine = build_engine(settings)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """One transaction per request; every service flushes but never commits."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
