"""Test defaults. Do not override a real DATABASE_URL from the environment."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben",
)
os.environ.setdefault("BEN_DB_CONNECT_TIMEOUT_S", "2")
