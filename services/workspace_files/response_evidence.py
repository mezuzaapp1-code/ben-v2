"""BEN ResponseEvidence V1 — injected-only provenance for Sources.

Built once at context assembly from units that actually entered the prompt.
Never reconstructed from model text, filenames, source_state, or providers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable

RETRIEVAL_MODES = frozenset({"chunks", "prefix_fallback", "mixed"})
SOURCE_TYPE = "workspace_file"
ORIGIN = "ben_retrieval"

MAX_EXCERPT_CHARS = 400
MAX_EVIDENCE_ITEMS = 12
MAX_SOURCES = 8
MAX_TOTAL_EXCERPT_CHARS = 2400
MAX_EVIDENCE_ID_CHARS = 80


@dataclass(frozen=True)
class EvidenceUnit:
    """One injected unit. Prefix rows must omit chunk_id and page."""

    source_id: str
    display_name: str
    excerpt: str
    chunk_id: str | None = None
    page: int | None = None


def clip_excerpt(text: str | None) -> str:
    """Prefix of the already-injected unit. Unicode code points."""
    body = str(text or "")
    if len(body) <= MAX_EXCERPT_CHARS:
        return body
    return body[:MAX_EXCERPT_CHARS]


def _sanitize_display_name(name: str | None) -> str:
    cleaned = " ".join(str(name or "file").split())
    return cleaned.replace('"', "'")[:256] or "file"


def _uuid_str(raw: Any) -> str | None:
    try:
        return str(uuid.UUID(str(raw).strip()))
    except (TypeError, ValueError, AttributeError):
        return None


def _page_int(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float) and not raw.is_integer():
        return None
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return None
    return page if page >= 1 else None


def units_from_budgeted(budgeted: Iterable[Any]) -> list[EvidenceUnit]:
    """Prefix units from already-budgeted Gate 3D / cover-fill files."""
    units: list[EvidenceUnit] = []
    for item in budgeted or ():
        fid = str(getattr(item, "file_id", "") or "").strip()
        if not fid:
            continue
        units.append(
            EvidenceUnit(
                source_id=fid,
                display_name=str(getattr(item, "name", "") or ""),
                excerpt=str(getattr(item, "text", "") or ""),
            )
        )
    return units


def units_from_chunk_hits(grouped: Iterable[Any], by_id: dict[str, Any]) -> list[EvidenceUnit]:
    """Chunk units from already-budgeted Gate 4A hits, file order preserved."""
    units: list[EvidenceUnit] = []
    for fid, chunks in grouped or ():
        meta = by_id.get(str(fid))
        if meta is None:
            continue
        name = str(getattr(meta, "display_name", "") or getattr(meta, "original_filename", "") or "")
        for hit in chunks or ():
            units.append(
                EvidenceUnit(
                    source_id=str(getattr(hit, "file_id", "") or ""),
                    display_name=name,
                    excerpt=str(getattr(hit, "text", "") or ""),
                    chunk_id=str(getattr(hit, "chunk_id", "") or "") or None,
                    page=getattr(hit, "page_number", None),
                )
            )
    return units


def _item_from_unit(unit: EvidenceUnit, source_id: str) -> dict[str, Any] | None:
    excerpt = clip_excerpt(unit.excerpt)
    if not excerpt:
        return None
    chunk_id = _uuid_str(unit.chunk_id) if unit.chunk_id else None
    if chunk_id:
        evidence_id = f"chunk:{chunk_id}"
        item: dict[str, Any] = {
            "evidence_id": evidence_id[:MAX_EVIDENCE_ID_CHARS],
            "source_id": source_id,
            "excerpt": excerpt,
            "origin": ORIGIN,
            "chunk_id": chunk_id,
        }
        page = _page_int(unit.page)
        if page is not None:
            item["page"] = page
        return item
    # Prefix: never page or chunk_id, even if a caller passed them.
    evidence_id = f"prefix:{source_id}"
    return {
        "evidence_id": evidence_id[:MAX_EVIDENCE_ID_CHARS],
        "source_id": source_id,
        "excerpt": excerpt,
        "origin": ORIGIN,
    }


def build_response_evidence(
    *,
    retrieval_mode: str,
    units: Iterable[EvidenceUnit] | None,
) -> dict[str, Any] | None:
    """Canonical constructor. Empty / invalid → None."""
    mode = str(retrieval_mode or "").strip()
    if mode not in RETRIEVAL_MODES:
        return None
    sources: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    evidence: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    total = 0
    for unit in units or ():
        if not isinstance(unit, EvidenceUnit):
            continue
        source_id = _uuid_str(unit.source_id)
        if not source_id:
            continue
        name = _sanitize_display_name(unit.display_name)
        if source_id not in seen_sources:
            if len(sources) >= MAX_SOURCES:
                continue
            seen_sources.add(source_id)
            sources.append(
                {
                    "source_id": source_id,
                    "source_type": SOURCE_TYPE,
                    "display_name": name,
                }
            )
        item = _item_from_unit(unit, source_id)
        if item is None:
            continue
        eid = item["evidence_id"]
        if not eid or eid in seen_ids:
            continue
        excerpt = item["excerpt"]
        if len(evidence) >= MAX_EVIDENCE_ITEMS:
            continue
        if total + len(excerpt) > MAX_TOTAL_EXCERPT_CHARS:
            continue
        seen_ids.add(eid)
        evidence.append(item)
        total += len(excerpt)
    if not sources:
        return None
    return {
        "retrieval_mode": mode,
        "sources": sources,
        "evidence": evidence,
    }


def sanitize_response_evidence(raw: Any) -> dict[str, Any] | None:
    """Fail-closed encode/decode sanitizer. Same caps as the constructor."""
    if not isinstance(raw, dict):
        return None
    mode = str(raw.get("retrieval_mode") or "").strip()
    if mode not in RETRIEVAL_MODES:
        return None
    raw_sources = raw.get("sources")
    raw_evidence = raw.get("evidence")
    if not isinstance(raw_sources, (list, tuple)):
        return None
    names: dict[str, str] = {}
    sources: list[dict[str, str]] = []
    for src in raw_sources:
        if not isinstance(src, dict):
            continue
        sid = _uuid_str(src.get("source_id"))
        if not sid or sid in names:
            continue
        if str(src.get("source_type") or SOURCE_TYPE).strip() != SOURCE_TYPE:
            continue
        names[sid] = _sanitize_display_name(src.get("display_name"))
        sources.append(
            {
                "source_id": sid,
                "source_type": SOURCE_TYPE,
                "display_name": names[sid],
            }
        )
        if len(sources) >= MAX_SOURCES:
            break
    if not sources:
        return None
    evidence: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    total = 0
    if isinstance(raw_evidence, (list, tuple)):
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            sid = _uuid_str(item.get("source_id"))
            if not sid or sid not in names:
                continue
            if str(item.get("origin") or "").strip() != ORIGIN:
                continue
            chunk_raw = item.get("chunk_id")
            unit = EvidenceUnit(
                source_id=sid,
                display_name=names[sid],
                excerpt=str(item.get("excerpt") or ""),
                chunk_id=str(chunk_raw).strip() if chunk_raw else None,
                page=item.get("page") if chunk_raw else None,
            )
            clean = _item_from_unit(unit, sid)
            if clean is None:
                continue
            eid = clean["evidence_id"]
            if eid in seen_ids or len(evidence) >= MAX_EVIDENCE_ITEMS:
                continue
            if total + len(clean["excerpt"]) > MAX_TOTAL_EXCERPT_CHARS:
                continue
            seen_ids.add(eid)
            evidence.append(clean)
            total += len(clean["excerpt"])
    return {"retrieval_mode": mode, "sources": sources, "evidence": evidence}
