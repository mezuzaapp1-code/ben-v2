import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

load_dotenv()


def _db_connect_timeout_s() -> float:
    raw = os.getenv("BEN_DB_CONNECT_TIMEOUT_S", "10").strip()
    try:
        return float(raw)
    except ValueError:
        return 10.0


def _create_engine():
    url = os.environ["DATABASE_URL"]
    connect_args: dict = {}
    if url.startswith("postgresql"):
        connect_args["timeout"] = _db_connect_timeout_s()
    return create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        pool_size=int(os.getenv("BEN_DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("BEN_DB_POOL_MAX_OVERFLOW", "5")),
        connect_args=connect_args,
    )


_engine = _create_engine()
SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def get_db_session():
    async with SessionLocal() as session:
        yield session


def get_engine():
    return _engine


async def warmup_database_pool() -> bool:
    """Establish a pooled connection at startup so health/council pings stay fast."""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    """Dispose the shared async engine (CLI / cron exit hygiene)."""
    await _engine.dispose()
