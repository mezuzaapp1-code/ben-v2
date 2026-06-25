"""Database-per-thread SQLite storage: system_main metadata + isolated thread message files."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
_SYSTEM_DB = _DATA_ROOT / "system_main.db"
_THREADS_DIR = _DATA_ROOT / "threads"
_PROJECT_CONTEXT_DB = "project_context.db"

_THREAD_ID_SAFE = re.compile(r"[^a-zA-Z0-9\-]")


def system_db_path() -> Path:
    raw = (os.getenv("BEN_SYSTEM_DB_PATH") or "").strip()
    return Path(raw) if raw else _SYSTEM_DB


def threads_data_dir() -> Path:
    raw = (os.getenv("BEN_THREADS_DATA_DIR") or "").strip()
    return Path(raw) if raw else _THREADS_DIR


def _sanitize_thread_id(thread_id: str) -> str:
    tid = str(thread_id or "").strip()
    if not tid:
        raise ValueError("thread_id is required")
    return _THREAD_ID_SAFE.sub("", tid)


def legacy_thread_db_path(thread_id: str) -> Path:
    """Global per-thread SQLite path for standard chat conversations."""
    safe = _sanitize_thread_id(thread_id)
    return threads_data_dir() / f"thread_{safe}.db"


def project_context_db_path(project_slug: str) -> Path:
    """Portable portfolio SQLite path inside the physical project folder."""
    from services.project_tools import projects_root, slugify_project_name

    slug = slugify_project_name(project_slug)
    return (projects_root() / slug / _PROJECT_CONTEXT_DB).resolve()


def resolve_thread_db_path(thread_id: str) -> Path:
    """Resolve SQLite path from system_main metadata (portable vs global)."""
    meta = get_thread_metadata(thread_id)
    if meta and str(meta.get("session_type") or "") == "project_setup":
        slug = str(meta.get("project_slug") or "").strip()
        if slug:
            return project_context_db_path(slug)
    return legacy_thread_db_path(thread_id)


def thread_db_path(thread_id: str) -> Path:
    return resolve_thread_db_path(thread_id)


def _unlink_sqlite_cluster(path: Path) -> bool:
    removed = False
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            for attempt in range(3):
                try:
                    candidate.unlink()
                    removed = True
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)
    return removed


@contextmanager
def get_thread_db_connection(thread_id: str) -> Iterator[sqlite3.Connection]:
    """Open an isolated thread DB, init schema, and close when the context exits."""
    path = resolve_thread_db_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                provider TEXT,
                message_type TEXT NOT NULL DEFAULT 'normal',
                content TEXT NOT NULL,
                insert_after_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (insert_after_id) REFERENCES messages(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_thread_messages_created
                ON messages(created_at);
            CREATE INDEX IF NOT EXISTS idx_thread_messages_insert_after
                ON messages(insert_after_id);
            """
        )
        conn.commit()
        yield conn
    finally:
        conn.close()


@contextmanager
def get_system_db_connection() -> Iterator[sqlite3.Connection]:
    path = system_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                title TEXT NOT NULL,
                project_id TEXT,
                session_type TEXT NOT NULL DEFAULT 'chat',
                project_slug TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_threads_org_updated
                ON threads(org_id, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_threads_org_project_slug
                ON threads(org_id, project_slug);
            """
        )
        _ensure_system_columns(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()


def _ensure_system_columns(conn: sqlite3.Connection) -> None:
    for ddl in (
        "ALTER TABLE threads ADD COLUMN session_type TEXT NOT NULL DEFAULT 'chat'",
        "ALTER TABLE threads ADD COLUMN project_slug TEXT",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass


def init_thread_store() -> None:
    with get_system_db_connection() as conn:
        _ensure_system_columns(conn)
        conn.commit()
    from services.global_service_store import init_global_service_schema

    init_global_service_schema()


@dataclass(frozen=True)
class ThreadStoreMessage:
    id: int
    role: str
    provider: str | None
    message_type: str
    content: str
    insert_after_id: int | None
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "provider": self.provider,
            "message_type": self.message_type,
            "content": self.content,
            "insert_after_id": self.insert_after_id,
            "created_at": self.created_at,
        }


def _row_to_message(row: sqlite3.Row) -> ThreadStoreMessage:
    return ThreadStoreMessage(
        id=int(row["id"]),
        role=str(row["role"]),
        provider=row["provider"],
        message_type=str(row["message_type"] or "normal"),
        content=str(row["content"]),
        insert_after_id=int(row["insert_after_id"]) if row["insert_after_id"] is not None else None,
        created_at=str(row["created_at"]),
    )


def order_thread_messages(rows: list[ThreadStoreMessage]) -> list[ThreadStoreMessage]:
    """Linearize messages; expert inserts appear directly below their anchor."""
    if not rows:
        return []
    by_id = {m.id: m for m in rows}
    children: dict[int, list[ThreadStoreMessage]] = {}
    roots: list[ThreadStoreMessage] = []
    for msg in rows:
        anchor = msg.insert_after_id
        if anchor is not None and anchor in by_id:
            children.setdefault(anchor, []).append(msg)
        else:
            roots.append(msg)
    roots.sort(key=lambda m: m.id)

    ordered: list[ThreadStoreMessage] = []

    def walk(node: ThreadStoreMessage) -> None:
        ordered.append(node)
        for child in sorted(children.get(node.id, []), key=lambda m: m.id):
            walk(child)

    for root in roots:
        walk(root)

    seen = {m.id for m in ordered}
    for msg in sorted(rows, key=lambda m: m.id):
        if msg.id not in seen:
            ordered.append(msg)
    return ordered


def upsert_thread_metadata(
    *,
    thread_id: str,
    org_id: str,
    title: str,
    project_id: str | None = None,
    session_type: str = "chat",
    project_slug: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_system_db_connection() as conn:
        _ensure_system_columns(conn)
        conn.execute(
            """
            INSERT INTO threads (id, org_id, title, project_id, session_type, project_slug, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                project_id = COALESCE(excluded.project_id, threads.project_id),
                session_type = COALESCE(excluded.session_type, threads.session_type),
                project_slug = COALESCE(excluded.project_slug, threads.project_slug),
                updated_at = excluded.updated_at
            """,
            (
                str(thread_id),
                str(org_id),
                (title or "Conversation")[:512],
                project_id,
                (session_type or "chat")[:64],
                project_slug,
                now,
                now,
            ),
        )
        conn.commit()


def get_thread_session_type(thread_id: str) -> str:
    with get_system_db_connection() as conn:
        _ensure_system_columns(conn)
        row = conn.execute(
            "SELECT session_type FROM threads WHERE id = ?",
            (str(thread_id),),
        ).fetchone()
    if row is None:
        return "chat"
    return str(row["session_type"] or "chat")


def get_thread_metadata(thread_id: str) -> dict[str, Any] | None:
    with get_system_db_connection() as conn:
        _ensure_system_columns(conn)
        row = conn.execute(
            """
            SELECT id, org_id, title, project_id, session_type, project_slug, created_at, updated_at
            FROM threads WHERE id = ?
            """,
            (str(thread_id),),
        ).fetchone()
    return dict(row) if row else None


def get_thread_project_slug(thread_id: str) -> str | None:
    """Fast metadata-only slug lookup (no message scan or filesystem probes)."""
    meta = get_thread_metadata(thread_id)
    if not meta:
        return None
    slug = str(meta.get("project_slug") or "").strip()
    return slug or None


def delete_thread_metadata(thread_id: str, org_id: str) -> bool:
    with get_system_db_connection() as conn:
        _ensure_system_columns(conn)
        cur = conn.execute(
            "DELETE FROM threads WHERE id = ? AND org_id = ?",
            (str(thread_id), str(org_id)),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0


def delete_thread_database_file(thread_id: str) -> bool:
    """Remove isolated thread SQLite file and WAL sidecars at the resolved path."""
    return _unlink_sqlite_cluster(resolve_thread_db_path(thread_id))


def release_thread_database_files(thread_id: str) -> None:
    """Drop SQLite cluster handles before removing the containing project folder."""
    _unlink_sqlite_cluster(resolve_thread_db_path(thread_id))


def promote_thread_to_portable_storage(
    *,
    thread_id: str,
    org_id: str,
    project_slug: str,
) -> dict[str, Any]:
    """Move a standard chat SQLite file into data/projects/{slug}/project_context.db."""
    meta = get_thread_metadata(thread_id)
    if meta is None or str(meta.get("org_id")) != str(org_id):
        raise ValueError("thread not found")
    if str(meta.get("session_type") or "") == "project_setup":
        raise ValueError("thread is already a project workspace")

    from services.project_tools import create_project_directory, slugify_project_name

    slug = slugify_project_name(project_slug)
    create_project_directory(slug)
    dest_db = project_context_db_path(slug)
    if dest_db.exists():
        raise ValueError("project slug already in use")

    src = legacy_thread_db_path(thread_id)
    if src.exists():
        shutil.move(str(src), str(dest_db))
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{src}{suffix}")
            if sidecar.exists():
                shutil.move(str(sidecar), str(Path(f"{dest_db}{suffix}")))

    upsert_thread_metadata(
        thread_id=thread_id,
        org_id=org_id,
        title=str(meta.get("title") or "Project")[:512],
        project_id=meta.get("project_id"),
        session_type="project_setup",
        project_slug=slug,
    )
    return {
        "thread_id": str(thread_id),
        "project_slug": slug,
        "sqlite_path": str(dest_db),
        "session_type": "project_setup",
    }


def touch_thread_metadata(thread_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_system_db_connection() as conn:
        conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, str(thread_id)))
        conn.commit()


def list_thread_metadata(org_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    with get_system_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, org_id, title, project_id, created_at, updated_at
            FROM threads
            WHERE org_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (str(org_id), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def insert_thread_message(
    thread_id: str,
    *,
    role: str,
    content: str,
    provider: str | None = None,
    message_type: str = "normal",
    insert_after_id: int | None = None,
) -> int:
    with get_thread_db_connection(thread_id) as conn:
        cur = conn.execute(
            """
            INSERT INTO messages (role, provider, message_type, content, insert_after_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (role, provider, message_type, content, insert_after_id),
        )
        conn.commit()
        message_id = int(cur.lastrowid)
    touch_thread_metadata(thread_id)
    return message_id


def list_thread_messages(thread_id: str) -> list[ThreadStoreMessage]:
    with get_thread_db_connection(thread_id) as conn:
        rows = conn.execute(
            """
            SELECT id, role, provider, message_type, content, insert_after_id, created_at
            FROM messages
            ORDER BY id ASC
            """
        ).fetchall()
    return order_thread_messages([_row_to_message(row) for row in rows])


def list_thread_messages_until(thread_id: str, anchor_message_id: int | None) -> list[ThreadStoreMessage]:
    """Messages up to and including anchor (in display order); full thread when anchor is None."""
    ordered = list_thread_messages(thread_id)
    if anchor_message_id is None:
        return ordered
    result: list[ThreadStoreMessage] = []
    for msg in ordered:
        result.append(msg)
        if msg.id == int(anchor_message_id):
            break
    return result


def get_thread_message(thread_id: str, message_id: int) -> ThreadStoreMessage | None:
    with get_thread_db_connection(thread_id) as conn:
        row = conn.execute(
            """
            SELECT id, role, provider, message_type, content, insert_after_id, created_at
            FROM messages WHERE id = ?
            """,
            (int(message_id),),
        ).fetchone()
    return _row_to_message(row) if row else None
