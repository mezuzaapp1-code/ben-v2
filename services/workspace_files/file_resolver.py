"""Gate 3D — select/rank eligible Workspace Files before applying context budget.

Invariant: FILE SELECTION MUST PRECEDE CONTEXT BUDGETING.

Eligible READY files are collected and ranked first. Only the ranked list is
then clipped to the per-file and global character budgets. Database order and
the remaining budget must never decide which files are considered relevant.

This is lexical/filename selection only: no embeddings, no vector index, no
chunk retrieval, no provider calls.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

# One file cannot consume the whole global chat-context budget.
PER_FILE_MAX_CHARS = int(os.getenv("BEN_WORKSPACE_FILES_PER_FILE_MAX_CHARS", "2000"))

# Explicit filename mention outranks any token overlap.
_EXPLICIT_SCORE = 1_000_000
_FILENAME_TOKEN_SCORE = 1_000
_BODY_TOKEN_SCORE = 1

# Deterministic tokenizer: latin + Hebrew letters/digits. Underscores and
# punctuation split tokens so ``ben_canary.txt`` yields ``ben`` + ``canary``.
_TOKEN_RE = re.compile(r"[0-9A-Za-z\u0590-\u05FF]+")

# Query words that must not create false relevance (selection stays lexical).
_STOPWORDS = frozenset(
    {
        "about",
        "and",
        "are",
        "can",
        "csv",
        "does",
        "docx",
        "file",
        "files",
        "for",
        "from",
        "give",
        "into",
        "jpeg",
        "jpg",
        "json",
        "md",
        "pdf",
        "please",
        "png",
        "read",
        "show",
        "tell",
        "that",
        "the",
        "this",
        "txt",
        "what",
        "with",
        "xlsx",
        "you",
        "your",
    }
)


@dataclass(frozen=True)
class EligibleFile:
    """A READY file that already passed org/workspace/status/text eligibility."""

    id: Any
    created_at: Any
    display_name: str
    original_filename: str
    text: str


@dataclass(frozen=True)
class RankedFile:
    file: EligibleFile
    score: int
    explicit_name: bool


@dataclass(frozen=True)
class BudgetedFile:
    name: str
    text: str
    chars: int
    clipped: bool
    file_id: str = ""


def _tokens(text: str) -> frozenset[str]:
    return frozenset(
        t for t in _TOKEN_RE.findall((text or "").casefold()) if len(t) >= 3 and t not in _STOPWORDS
    )


def significant_tokens_in_order(
    text: str,
    *,
    extra_stopwords: frozenset[str] | None = None,
    limit: int | None = None,
) -> list[str]:
    """First-seen significant tokens. Does not change Gate 3D rank math."""
    stop = _STOPWORDS if not extra_stopwords else (_STOPWORDS | extra_stopwords)
    seen: set[str] = set()
    out: list[str] = []
    for token in _TOKEN_RE.findall((text or "").casefold()):
        if len(token) < 3 or token in stop or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if limit is not None and len(out) >= limit:
            break
    return out


def file_is_explicitly_named(user_query: str, display_name: str, original_filename: str) -> bool:
    """True when the query mentions this file's display name, basename, or stem."""
    return _explicit_name_match(
        user_query,
        EligibleFile(
            id=None,
            created_at=None,
            display_name=display_name,
            original_filename=original_filename,
            text="",
        ),
    )


def _name_candidates(file: EligibleFile) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in (file.display_name, file.original_filename):
        raw = " ".join(str(raw or "").split())
        if not raw:
            continue
        basename = PurePosixPath(raw.replace("\\", "/")).name
        stem = PurePosixPath(basename).stem
        for candidate in (raw, basename, stem):
            key = candidate.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def _explicit_name_match(query: str, file: EligibleFile) -> bool:
    query_cf = (query or "").casefold()
    if not query_cf.strip():
        return False
    for name in _name_candidates(file):
        key = name.casefold()
        suffix = PurePosixPath(name).suffix
        # Bare stems like "a" from "a.txt" are too short to count as a mention.
        if not suffix and len(key) < 3:
            continue
        if len(key) < 2:
            continue
        if key in query_cf:
            return True
    return False


def score_file(file: EligibleFile, user_query: str) -> RankedFile:
    """Deterministic relevance score. Higher is selected earlier."""
    explicit = _explicit_name_match(user_query, file)
    score = _EXPLICIT_SCORE if explicit else 0
    query_tokens = _tokens(user_query)
    if query_tokens:
        filename_tokens = _tokens(
            " ".join(_name_candidates(file))
        )
        body_tokens = _tokens(file.text)
        score += _FILENAME_TOKEN_SCORE * len(query_tokens & filename_tokens)
        score += _BODY_TOKEN_SCORE * len(query_tokens & body_tokens)
    return RankedFile(file=file, score=score, explicit_name=explicit)


def rank_eligible_files(files: list[EligibleFile], user_query: str | None) -> list[RankedFile]:
    """Rank every eligible file. Does not apply any character budget."""
    ranked = [score_file(f, user_query or "") for f in files]
    ranked.sort(key=_rank_sort_key)
    return ranked


def _rank_sort_key(r: RankedFile) -> tuple:
    # score DESC, created_at DESC (None last), id DESC.
    created = r.file.created_at
    return (
        -r.score,
        created is None,
        _Desc(created) if created is not None else None,
        _Desc(r.file.id),
    )


class _Desc:
    """Ascending-sort wrapper that compares in reverse."""

    __slots__ = ("value",)

    def __init__(self, value: Any):
        self.value = value

    def __lt__(self, other: "_Desc") -> bool:
        try:
            return self.value > other.value
        except TypeError:
            return str(self.value) > str(other.value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Desc):
            return NotImplemented
        return self.value == other.value


def apply_context_budget(
    ranked: list[RankedFile],
    *,
    max_chars: int,
    per_file_max: int | None = None,
    sanitize_name,
) -> tuple[list[BudgetedFile], bool]:
    """Clip already-selected files to per-file and global budgets.

    ``truncated`` is True when any eligible text was omitted (skipped file or clip).
    """
    if max_chars <= 0 or not ranked:
        return [], False
    limit = per_file_max if per_file_max is not None else PER_FILE_MAX_CHARS
    if limit <= 0:
        return [], True

    out: list[BudgetedFile] = []
    total = 0
    truncated = False
    for item in ranked:
        remaining = max_chars - total
        if remaining <= 0:
            truncated = True
            break
        body = item.file.text
        allowed = min(limit, remaining)
        clipped = False
        if len(body) > allowed:
            body = body[:allowed]
            clipped = True
            truncated = True
        if not body:
            continue
        name = sanitize_name(item.file.display_name or item.file.original_filename)
        file_id = str(item.file.id) if getattr(item.file, "id", None) is not None else ""
        out.append(
            BudgetedFile(
                name=name,
                text=body,
                chars=len(body),
                clipped=clipped,
                file_id=file_id,
            )
        )
        total += len(body)
    return out, truncated


def eligible_from_row(row: Any, org_id: Any, workspace_id: Any) -> EligibleFile | None:
    """Defense-in-depth eligibility. Ranking never sees a non-eligible row."""
    if str(row.org_id) != str(org_id) or str(row.workspace_id) != str(workspace_id):
        return None
    if getattr(row, "status", None) != "ready":
        return None
    body = (getattr(row, "extracted_text", None) or "").strip()
    if not body:
        return None
    return EligibleFile(
        id=row.id,
        created_at=getattr(row, "created_at", None),
        display_name=str(getattr(row, "display_name", None) or ""),
        original_filename=str(getattr(row, "original_filename", None) or ""),
        text=body,
    )
