"""Lightweight SQLite knowledge base — decoupled from main Postgres chat pipeline."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "knowledge.db"


def knowledge_db_path() -> Path:
    raw = (os.getenv("BEN_KNOWLEDGE_DB_PATH") or "").strip()
    return Path(raw) if raw else _DEFAULT_DB


def _connect() -> sqlite3.Connection:
    path = knowledge_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_knowledge_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kb_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_documents_kb_id
                ON knowledge_documents(kb_id);
            """
        )
        conn.commit()


def get_connection() -> sqlite3.Connection:
    init_knowledge_store()
    return _connect()
