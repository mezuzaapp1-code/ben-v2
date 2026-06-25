"""Knowledge base CRUD and few-shot retrieval (SQLite, indexed by kb_id)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from database.knowledge_store import get_connection

logger = logging.getLogger("ben.knowledge_service")

_MAX_FEW_SHOT_DOCS = 4
_MAX_FEW_SHOT_CHARS = 6000


def _row_to_base(row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "created_at": row["created_at"]}


def _row_to_doc(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kb_id": row["kb_id"],
        "title": row["title"],
        "content": row["content"],
    }


def _list_bases_sync() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM knowledge_bases ORDER BY name COLLATE NOCASE ASC"
        ).fetchall()
    return [_row_to_base(r) for r in rows]


def _create_base_sync(name: str) -> dict[str, Any]:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Knowledge base name is required")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO knowledge_bases (name) VALUES (?)",
            (clean,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, name, created_at FROM knowledge_bases WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return _row_to_base(row)


def _delete_base_sync(base_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (base_id,))
        conn.commit()
    return cur.rowcount > 0


def _list_docs_sync(base_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, kb_id, title, content FROM knowledge_documents "
            "WHERE kb_id = ? ORDER BY id ASC",
            (base_id,),
        ).fetchall()
    return [_row_to_doc(r) for r in rows]


def _add_doc_sync(base_id: int, *, title: str, content: str) -> dict[str, Any]:
    t = (title or "").strip() or "Untitled"
    body = (content or "").strip()
    if not body:
        raise ValueError("Document content is required")
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM knowledge_bases WHERE id = ?",
            (base_id,),
        ).fetchone()
        if not exists:
            raise LookupError("Knowledge base not found")
        cur = conn.execute(
            "INSERT INTO knowledge_documents (kb_id, title, content) VALUES (?, ?, ?)",
            (base_id, t[:512], body),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, kb_id, title, content FROM knowledge_documents WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return _row_to_doc(row)


def _delete_doc_sync(doc_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM knowledge_documents WHERE id = ?", (doc_id,))
        conn.commit()
    return cur.rowcount > 0


def _match_base_names_sync(message: str) -> list[str]:
    text = (message or "").strip()
    if not text:
        return []
    bases = _list_bases_sync()
    matched: list[str] = []
    lower = text.lower()
    for base in bases:
        name = str(base["name"]).strip()
        if not name:
            continue
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            matched.append(name)
            continue
        if name.lower() in lower:
            matched.append(name)
    return matched


def _few_shot_block_sync(message: str) -> str:
    names = _match_base_names_sync(message)
    if not names:
        return ""
    blocks: list[str] = []
    total = 0
    with get_connection() as conn:
        for name in names:
            base = conn.execute(
                "SELECT id, name FROM knowledge_bases WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if not base:
                continue
            docs = conn.execute(
                "SELECT title, content FROM knowledge_documents WHERE kb_id = ? "
                "ORDER BY id ASC LIMIT ?",
                (base["id"], _MAX_FEW_SHOT_DOCS),
            ).fetchall()
            for doc in docs:
                chunk = (
                    f"### Example from {base['name']}: {doc['title']}\n"
                    f"{str(doc['content']).strip()}"
                )
                if total + len(chunk) > _MAX_FEW_SHOT_CHARS:
                    return "\n\n".join(blocks) if blocks else chunk[:_MAX_FEW_SHOT_CHARS]
                blocks.append(chunk)
                total += len(chunk)
    return "\n\n".join(blocks)


async def list_knowledge_bases() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_bases_sync)


async def create_knowledge_base(name: str) -> dict[str, Any]:
    return await asyncio.to_thread(_create_base_sync, name)


async def delete_knowledge_base(base_id: int) -> bool:
    return await asyncio.to_thread(_delete_base_sync, base_id)


async def list_knowledge_documents(base_id: int) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_docs_sync, base_id)


async def add_knowledge_document(base_id: int, *, title: str, content: str) -> dict[str, Any]:
    return await asyncio.to_thread(_add_doc_sync, base_id, title=title, content=content)


async def delete_knowledge_document(doc_id: int) -> bool:
    return await asyncio.to_thread(_delete_doc_sync, doc_id)


async def build_knowledge_few_shot_block(
    message: str,
    context_id: str | None = None,
) -> str:
    """Indexed SQLite lookup — runs off the hot token stream path."""
    if context_id:
        logger.debug("context_id received but not used for filtering yet: %s", context_id)
    return await asyncio.to_thread(_few_shot_block_sync, message)