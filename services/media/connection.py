"""Dedicated least-privileged media database pool; no admin URL fallback."""
from contextlib import asynccontextmanager
from functools import lru_cache
import os

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@lru_cache(maxsize=1)
def media_sessions():
    url = os.getenv("BEN_MEDIA_DATABASE_URL", "").strip()
    if not url:
        raise HTTPException(503, "Media database unavailable")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not url.startswith("postgresql+asyncpg://"):
        raise HTTPException(503, "Media database unavailable")
    engine = create_async_engine(url, echo=False, pool_pre_ping=True,
                                 pool_size=2, max_overflow=2,
                                 connect_args={"timeout": 10})
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_media_session():
    async with media_sessions()() as session:
        yield session
