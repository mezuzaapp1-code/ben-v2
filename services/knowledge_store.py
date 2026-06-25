"""Portable project context store — hybrid attention retrieval and multi-head prompt assembly."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from database.thread_store import project_context_db_path

HEAD_CODE = "code"
HEAD_DOCUMENTATION = "documentation"
HEAD_HISTORY = "history"

ALL_HEADS = (HEAD_CODE, HEAD_DOCUMENTATION, HEAD_HISTORY)

_EMBED_DIM = 64
_SEMANTIC_WEIGHT = 0.45
_RECENCY_WEIGHT = 0.30
_FTS_WEIGHT = 0.25
_RECENCY_HALF_LIFE_DAYS = 14.0
_TOKEN_RE = re.compile(r"\w+")

_MAX_KNOWLEDGE_UPLOAD_BYTES = 500 * 1024 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._\-]+")

HEAD_TYPE_LABELS: dict[str, str] = {
    HEAD_CODE: "Code",
    HEAD_DOCUMENTATION: "Doc",
    HEAD_HISTORY: "History",
}

HEAD_TYPE_ICONS: dict[str, str] = {
    HEAD_CODE: "💻",
    HEAD_DOCUMENTATION: "📄",
    HEAD_HISTORY: "🕒",
}


class UploadReader(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...


def resolve_project_db_path(project_slug: str) -> Path:
    """Portable portfolio DB: data/projects/{slug}/project_context.db."""
    return project_context_db_path(project_slug)


def resolve_project_knowledge_dir(project_slug: str) -> Path:
    """Passive tool-call storage: data/projects/{slug}/knowledge/."""
    from services.project_tools import projects_root, slugify_project_name

    slug = slugify_project_name(project_slug)
    root = projects_root().resolve()
    knowledge_dir = (root / slug / "knowledge").resolve()
    if root not in knowledge_dir.parents:
        raise ValueError("invalid project slug path")
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    return knowledge_dir


def _sanitize_filename(raw: str | None) -> str:
    name = Path(str(raw or "upload.bin")).name.strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", name).strip("._")
    return cleaned[:240] or "upload.bin"


def _unique_destination(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem[:200] or "upload"
    suffix = Path(filename).suffix[:20]
    token = uuid.uuid4().hex[:8]
    return directory / f"{stem}_{token}{suffix}"


def _connect(project_slug: str) -> sqlite3.Connection:
    path = resolve_project_db_path(project_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_portable_context_store(project_slug: str) -> None:
    """Ensure hybrid-attention and knowledge_store tables exist in the project SQLite portfolio."""
    with _connect(project_slug) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS context_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                head TEXT NOT NULL CHECK (head IN ('code', 'documentation', 'history')),
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_context_records_head
                ON context_records(head);
            CREATE INDEX IF NOT EXISTS idx_context_records_updated
                ON context_records(updated_at DESC);

            CREATE VIRTUAL TABLE IF NOT EXISTS context_records_fts USING fts5(
                record_id UNINDEXED,
                title,
                content,
                tokenize='porter unicode61'
            );

            CREATE TABLE IF NOT EXISTS knowledge_store (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE,
                absolute_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_type TEXT,
                sha256 TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready'
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_store_uploaded
                ON knowledge_store(uploaded_at DESC);
            """
        )
        conn.commit()


def _row_to_knowledge_file(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "filename": str(row["filename"]),
        "relative_path": str(row["relative_path"]),
        "size_bytes": int(row["size_bytes"]),
        "content_type": str(row["content_type"] or ""),
        "sha256": str(row["sha256"]),
        "uploaded_at": str(row["uploaded_at"]),
        "updated_at": str(row["updated_at"]),
        "status": str(row["status"]),
    }


def register_knowledge_file_metadata(
    project_slug: str,
    *,
    filename: str,
    relative_path: str,
    absolute_path: str,
    size_bytes: int,
    content_type: str | None,
    sha256: str,
    uploaded_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    init_portable_context_store(project_slug)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    uploaded = uploaded_at or now
    updated = updated_at or now
    with _connect(project_slug) as conn:
        cur = conn.execute(
            """
            INSERT INTO knowledge_store (
                filename, relative_path, absolute_path, size_bytes,
                content_type, sha256, uploaded_at, updated_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready')
            """,
            (
                filename[:512],
                relative_path,
                absolute_path,
                int(size_bytes),
                (content_type or "")[:128] or None,
                sha256,
                uploaded,
                updated,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM knowledge_store WHERE id = ?",
            (int(cur.lastrowid),),
        ).fetchone()
    if row is None:
        raise RuntimeError("knowledge metadata persist failed")
    return _row_to_knowledge_file(row)


def list_knowledge_files(project_slug: str) -> list[dict[str, Any]]:
    init_portable_context_store(project_slug)
    with _connect(project_slug) as conn:
        rows = conn.execute(
            """
            SELECT id, filename, relative_path, size_bytes, content_type,
                   sha256, uploaded_at, updated_at, status
            FROM knowledge_store
            WHERE status = 'ready'
            ORDER BY uploaded_at DESC, id DESC
            """
        ).fetchall()
    return [_row_to_knowledge_file(row) for row in rows]


async def stream_knowledge_upload(project_slug: str, upload: UploadReader) -> dict[str, Any]:
    """
    Stream an UploadFile to disk in fixed 1MB chunks (<=500MB total).
    Deletes partial files on failure/disconnect via try/finally cleanup.
    """
    init_portable_context_store(project_slug)
    knowledge_dir = resolve_project_knowledge_dir(project_slug)
    safe_name = _sanitize_filename(upload.filename)
    dest_path = _unique_destination(knowledge_dir, safe_name)
    hasher = hashlib.sha256()
    total_bytes = 0
    completed = False

    try:
        with dest_path.open("wb") as handle:
            while chunk := await upload.read(_STREAM_CHUNK_BYTES):
                if not chunk:
                    continue
                next_total = total_bytes + len(chunk)
                if next_total > _MAX_KNOWLEDGE_UPLOAD_BYTES:
                    raise ValueError("upload exceeds 500MB limit")
                handle.write(chunk)
                hasher.update(chunk)
                total_bytes = next_total

        if total_bytes == 0:
            raise ValueError("empty upload")

        slug_dir = knowledge_dir.parent
        relative_path = dest_path.relative_to(slug_dir).as_posix()
        record = register_knowledge_file_metadata(
            project_slug,
            filename=safe_name,
            relative_path=relative_path,
            absolute_path=str(dest_path.resolve()),
            size_bytes=total_bytes,
            content_type=upload.content_type,
            sha256=hasher.hexdigest(),
        )
        completed = True
        return record
    finally:
        if not completed and dest_path.exists():
            dest_path.unlink(missing_ok=True)


def _embed_text(text: str) -> list[float]:
    vec = [0.0] * _EMBED_DIM
    for token in _TOKEN_RE.findall((text or "").lower()):
        bucket = hash(token) % _EMBED_DIM
        vec[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _serialize_embedding(vec: list[float]) -> str:
    return json.dumps(vec, separators=(",", ":"))


def _deserialize_embedding(raw: str) -> list[float]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return _embed_text("")
    out = [float(x) for x in data[: _EMBED_DIM]]
    if len(out) < _EMBED_DIM:
        out.extend([0.0] * (_EMBED_DIM - len(out)))
    return out


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _parse_timestamp(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _recency_boost(updated_at: str, *, now: datetime | None = None) -> float:
    anchor = now or datetime.now(timezone.utc)
    age_days = max(0.0, (anchor - _parse_timestamp(updated_at)).total_seconds() / 86400.0)
    return math.exp(-age_days / _RECENCY_HALF_LIFE_DAYS)


def _fts_query_text(query_text: str) -> str:
    tokens = _TOKEN_RE.findall((query_text or "").lower())
    if not tokens:
        return ""
    return " OR ".join(tokens[:16])


def _fts_scores(conn: sqlite3.Connection, query_text: str) -> dict[int, float]:
    fts_q = _fts_query_text(query_text)
    if not fts_q:
        return {}
    rows = conn.execute(
        """
        SELECT record_id, bm25(context_records_fts) AS rank
        FROM context_records_fts
        WHERE context_records_fts MATCH ?
        ORDER BY rank
        LIMIT 64
        """,
        (fts_q,),
    ).fetchall()
    if not rows:
        return {}
    raw = [float(row["rank"]) for row in rows]
    # SQLite bm25: lower (more negative) is better match.
    best = min(raw)
    worst = max(raw)
    span = (worst - best) or 1.0
    out: dict[int, float] = {}
    for row, rank in zip(rows, raw):
        out[int(row["record_id"])] = (worst - rank) / span
    return out


def _normalize_component(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    span = (hi - lo) or 1.0
    return {key: (val - lo) / span for key, val in values.items()}


def insert_context_record(
    project_slug: str,
    *,
    head: str,
    title: str,
    content: str,
    uploaded_at: str | None = None,
    updated_at: str | None = None,
    embedding: list[float] | None = None,
) -> int:
    """Insert a scannable context row (used by tests and ingestion pipelines)."""
    if head not in ALL_HEADS:
        raise ValueError(f"invalid head: {head}")
    init_portable_context_store(project_slug)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    uploaded = uploaded_at or now
    updated = updated_at or now
    vec = embedding if embedding is not None else _embed_text(f"{title}\n{content}")
    with _connect(project_slug) as conn:
        cur = conn.execute(
            """
            INSERT INTO context_records (head, title, content, embedding, uploaded_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (head, title[:512], content, _serialize_embedding(vec), uploaded, updated),
        )
        record_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO context_records_fts (record_id, title, content) VALUES (?, ?, ?)",
            (record_id, title[:512], content),
        )
        conn.commit()
        return record_id


def query_hybrid_attention(
    project_slug: str,
    query_text: str,
    limit: int = 5,
    *,
    head: str | None = None,
) -> list[dict[str, Any]]:
    """
    Rank portable context records using semantic, recency, and FTS5 signals.

    Returns top items with explicit score breakdown for prompt assembly.
    """
    init_portable_context_store(project_slug)
    query_vec = _embed_text(query_text)
    now = datetime.now(timezone.utc)

    with _connect(project_slug) as conn:
        if head:
            rows = conn.execute(
                """
                SELECT id, head, title, content, embedding, uploaded_at, updated_at
                FROM context_records
                WHERE head = ?
                ORDER BY updated_at DESC
                """,
                (head,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, head, title, content, embedding, uploaded_at, updated_at
                FROM context_records
                ORDER BY updated_at DESC
                """
            ).fetchall()

        if not rows:
            return []

        fts_map = _fts_scores(conn, query_text)

        semantic_raw: dict[int, float] = {}
        recency_raw: dict[int, float] = {}
        fts_raw: dict[int, float] = {}

        for row in rows:
            rid = int(row["id"])
            semantic_raw[rid] = max(0.0, _dot(query_vec, _deserialize_embedding(str(row["embedding"]))))
            recency_raw[rid] = _recency_boost(str(row["updated_at"]), now=now)
            fts_raw[rid] = fts_map.get(rid, 0.0)

        semantic = _normalize_component(semantic_raw)
        recency = _normalize_component(recency_raw)
        fts_norm = _normalize_component(fts_raw) if any(fts_raw.values()) else fts_raw

        ranked: list[dict[str, Any]] = []
        for row in rows:
            rid = int(row["id"])
            sem = semantic.get(rid, 0.0)
            rec = recency.get(rid, 0.0)
            fts = fts_norm.get(rid, 0.0)
            final = (_SEMANTIC_WEIGHT * sem) + (_RECENCY_WEIGHT * rec) + (_FTS_WEIGHT * fts)
            ranked.append(
                {
                    "id": rid,
                    "head": str(row["head"]),
                    "title": str(row["title"]),
                    "content": str(row["content"]),
                    "uploaded_at": str(row["uploaded_at"]),
                    "updated_at": str(row["updated_at"]),
                    "final_score": round(final, 6),
                    "scores": {
                        "semantic": round(sem, 6),
                        "recency": round(rec, 6),
                        "fts": round(fts, 6),
                    },
                }
            )

    ranked.sort(key=lambda item: (-item["final_score"], -item["scores"]["recency"], item["id"]))
    return ranked[: max(1, int(limit))]


def build_multi_head_prompt_context(
    project_slug: str,
    query_text: str,
    *,
    limit_per_head: int = 3,
) -> str:
    """
    Assemble three structural prompt heads for high signal density:
    Code, Documentation, and History (promoted thread context).
    """
    sections: list[str] = []
    labels = {
        HEAD_CODE: "Code Head",
        HEAD_DOCUMENTATION: "Documentation Head",
        HEAD_HISTORY: "History Head",
    }
    for head in ALL_HEADS:
        hits = query_hybrid_attention(
            project_slug,
            query_text,
            limit=limit_per_head,
            head=head,
        )
        if not hits:
            continue
        blocks: list[str] = []
        for hit in hits:
            blocks.append(
                f"### {hit['title']} (score={hit['final_score']:.3f})\n"
                f"{hit['content'].strip()}"
            )
        sections.append(f"## {labels[head]}\n" + "\n\n".join(blocks))
    if not sections:
        return ""
    return (
        "<portable_project_context>\n"
        + "\n\n".join(sections)
        + "\n</portable_project_context>"
    )


def format_relative_timestamp(updated_at: str, *, now: datetime | None = None) -> str:
    anchor = now or datetime.now(timezone.utc)
    updated = _parse_timestamp(updated_at)
    delta = anchor - updated
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "Updated just now"
    minutes = seconds // 60
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"Updated {minutes} {unit} ago"
    hours = minutes // 60
    if hours < 24:
        unit = "hour" if hours == 1 else "hours"
        return f"Updated {hours} {unit} ago"
    days = hours // 24
    if days < 14:
        unit = "day" if days == 1 else "days"
        return f"Updated {days} {unit} ago"
    weeks = days // 7
    unit = "week" if weeks == 1 else "weeks"
    return f"Updated {weeks} {unit} ago"


def _format_attention_item(hit: dict[str, Any]) -> dict[str, Any]:
    head_key = str(hit["head"])
    scores = hit.get("scores") or {}
    sem = float(scores.get("semantic") or 0.0)
    rec = float(scores.get("recency") or 0.0)
    fts = float(scores.get("fts") or 0.0)
    final = float(hit.get("final_score") or 0.0)
    return {
        "entity_name": str(hit["title"]),
        "score": round(final, 4),
        "score_percent": round(final * 100, 1),
        "head_type": HEAD_TYPE_LABELS.get(head_key, head_key.title()),
        "head_key": head_key,
        "head_icon": HEAD_TYPE_ICONS.get(head_key, "•"),
        "updated_relative": format_relative_timestamp(str(hit["updated_at"])),
        "updated_at": str(hit["updated_at"]),
        "score_breakdown": {
            "semantic": round(sem, 4),
            "recency": round(rec, 4),
            "fts": round(fts, 4),
            "semantic_weighted": round(_SEMANTIC_WEIGHT * sem, 4),
            "recency_weighted": round(_RECENCY_WEIGHT * rec, 4),
            "fts_weighted": round(_FTS_WEIGHT * fts, 4),
        },
    }


def build_active_attention_focus(
    project_slug: str,
    query_text: str,
    *,
    limit_per_head: int = 3,
) -> dict[str, Any]:
    """Structured hybrid-attention snapshot for X-Ray focus UI."""
    query = (query_text or "").strip()
    items: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {
        HEAD_CODE: [],
        HEAD_DOCUMENTATION: [],
        HEAD_HISTORY: [],
    }

    if query:
        for head in ALL_HEADS:
            hits = query_hybrid_attention(
                project_slug,
                query,
                limit=max(1, int(limit_per_head)),
                head=head,
            )
            formatted = [_format_attention_item(hit) for hit in hits]
            grouped[head] = formatted
            items.extend(formatted)

    items.sort(key=lambda row: (-row["score"], row["entity_name"]))

    return {
        "project_slug": project_slug,
        "query": query,
        "items": items,
        "grouped": grouped,
        "weights": {
            "semantic": _SEMANTIC_WEIGHT,
            "recency": _RECENCY_WEIGHT,
            "fts": _FTS_WEIGHT,
        },
        "has_focus": bool(items),
    }


def assert_thread_matches_project_slug(project_slug: str, thread_id: str) -> None:
    """Raise ValueError when a persisted thread belongs to a different project slug."""
    from database.thread_store import get_thread_metadata
    from services.project_tools import slugify_project_name

    meta = get_thread_metadata(thread_id)
    if meta is None:
        return
    bound_slug = slugify_project_name(str(meta.get("project_slug") or ""))
    if bound_slug and bound_slug != slugify_project_name(project_slug):
        raise ValueError("thread not found for project")
