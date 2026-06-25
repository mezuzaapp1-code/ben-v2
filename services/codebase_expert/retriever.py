"""Bounded repository context loader for the Local Codebase Expert lane."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from services.ops.structured_log import log_warning

MAX_TOTAL_BYTES = int(os.getenv("BEN_CODEBASE_MAX_PACK_BYTES", "18000"))

CORE_ALWAYS: tuple[str, ...] = (
    "main.py",
    "services/council_service.py",
    "docs/SYSTEM_BOUNDARIES.md",
)

_REPO_ANCHOR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CodeContextPack:
    architecture_blurb: str
    files: list[dict]
    token_estimate: int

    @classmethod
    def from_file_entries(cls, entries: list[dict], *, max_bytes: int = MAX_TOTAL_BYTES) -> CodeContextPack:
        blurb = "BEN-V2: FastAPI backend (council/chat streams), React frontend, tenant-bound persistence."
        kept: list[dict] = []
        used = len(blurb.encode("utf-8"))
        for entry in entries:
            excerpt = str(entry.get("excerpt") or "")
            path = str(entry.get("path") or "")
            header = f"--- {path} ---\n"
            block = header + excerpt
            block_bytes = len(block.encode("utf-8"))
            if used + block_bytes > max_bytes:
                remaining = max_bytes - used - len(header.encode("utf-8"))
                if remaining > 64:
                    truncated = excerpt.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
                    kept.append({**entry, "excerpt": truncated, "truncated": True})
                    used = max_bytes
                break
            kept.append(entry)
            used += block_bytes
        return cls(architecture_blurb=blurb, files=kept, token_estimate=used)


def pack_is_usable(pack: CodeContextPack | None) -> bool:
    return pack is not None and len(pack.files) > 0


def _repo_root() -> Path:
    override = os.getenv("BEN_CODEBASE_ROOT", "").strip()
    if override in (".", "./"):
        return _REPO_ANCHOR
    if override:
        return Path(override).resolve()
    return _REPO_ANCHOR


def _read_file_entry(root: Path, rel_path: str) -> dict | None:
    path = root / rel_path
    if not path.is_file():
        log_warning(
            "codebase file missing",
            subsystem="codebase_expert",
            operation="read_file",
            path=str(path),
            root=str(root),
            outcome="missing",
        )
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log_warning(
            "codebase file read failed",
            subsystem="codebase_expert",
            operation="read_file",
            path=str(path),
            root=str(root),
            outcome="error",
            exc=e,
        )
        return None
    line_count = raw.count("\n") + (1 if raw else 0)
    return {
        "path": rel_path.replace("\\", "/"),
        "lines": f"1-{line_count}",
        "excerpt": raw,
        "relevance": "core",
    }


def _retrieve_files_sync(question: str) -> list[dict]:
    del question  # P1: static core bundle; keyword scoring in P3 manifest phase
    root = _repo_root()
    entries: list[dict] = []
    for rel_path in CORE_ALWAYS:
        entry = _read_file_entry(root, rel_path)
        if entry is not None:
            entries.append(entry)
    if not entries:
        log_warning(
            "codebase retrieval returned no files",
            subsystem="codebase_expert",
            operation="retrieve_files",
            root=str(root),
            outcome="empty",
        )
    return entries


async def retrieve_files(question: str) -> list[dict]:
    """Asynchronously load core repository files for the codebase expert."""
    return await asyncio.to_thread(_retrieve_files_sync, question)


def build_code_context_pack(question: str) -> CodeContextPack | None:
    """Build a byte-capped context pack from retrieved files."""
    pack = CodeContextPack.from_file_entries(_retrieve_files_sync(question))
    if not pack.files:
        return None
    return pack


async def build_code_context_pack_async(question: str) -> CodeContextPack | None:
    """Async wrapper for context pack construction."""
    entries = await retrieve_files(question)
    pack = CodeContextPack.from_file_entries(entries)
    if not pack.files:
        return None
    return pack
