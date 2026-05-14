import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

load_dotenv()
_engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def get_db_session():
    async with SessionLocal() as session:
        yield session


def get_engine():
    return _engine
