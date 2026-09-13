"""Fail-closed lexical expansion for existing simple FTS.

Default OFF. When enabled, query-prep only: extra ``to_tsquery('simple', ...)``
atoms derived from already-sanitized user tokens. Never expands from raw
user punctuation, never accepts ``:*`` from the user, never changes SQL
org/workspace/file filters.

Generic Latin inflection stripping only. No question-id hardcoding, no
document-specific synonym lists, no new dependencies.
"""

from __future__ import annotations

import os
import re
from typing import FrozenSet, List, Sequence

_LATIN_TOKEN_RE = re.compile(r"^[a-z0-9]+$")
_HEBREW_RE = re.compile(r"[\u0590-\u05ff]")
_SAFE_ATOM_RE = re.compile(r"^[0-9a-z\u0590-\u05ff]+(?::\*)?$")

# Longest-first inflectional suffixes. Applied only to Latin tokens.
# Conservative on purpose: derivational endings like ``ation`` over-stem.
_LATIN_SUFFIXES: tuple[str, ...] = (
    "ingly",
    "ings",
    "ied",
    "ies",
    "ily",
    "ing",
    "edly",
    "ed",
    "es",
    "ly",
    "s",
)

_MIN_TOKEN_LEN = 6
_MIN_STEM_LEN = 4
_MAX_EXTRA_ATOMS = 8

_DEFAULT_MODE = "off"
_ALLOWED_MODES = frozenset({"off", "prefix", "variants"})


def lexical_expand_mode() -> str:
    raw = (os.environ.get("BEN_FTS_LEXICAL_EXPAND") or _DEFAULT_MODE).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "prefix"
    if raw in _ALLOWED_MODES:
        return raw
    return _DEFAULT_MODE


def _latin_stems(token: str) -> List[str]:
    if not _LATIN_TOKEN_RE.fullmatch(token):
        return []
    if _HEBREW_RE.search(token):
        return []
    if len(token) < _MIN_TOKEN_LEN:
        return []
    for suffix in _LATIN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM_LEN:
            stem = token[: -len(suffix)]
            if stem != token and _LATIN_TOKEN_RE.fullmatch(stem):
                return [stem]
    return []


def extra_tsquery_atoms(tokens: Sequence[str], *, mode: str | None = None) -> List[str]:
    """Return extra simple-FTS atoms. ``tokens`` must already be sanitized."""
    resolved = (mode or lexical_expand_mode()).strip().lower()
    if resolved not in ("prefix", "variants"):
        return []
    extras: List[str] = []
    seen: set[str] = set(t.strip().lower() for t in tokens if t and t.strip())
    for token in tokens:
        t = (token or "").strip().lower()
        if not t:
            continue
        for stem in _latin_stems(t):
            atom = f"{stem}:*" if resolved == "prefix" else stem
            if not _SAFE_ATOM_RE.fullmatch(atom):
                continue
            if atom in seen or stem == t:
                continue
            seen.add(atom)
            extras.append(atom)
            if len(extras) >= _MAX_EXTRA_ATOMS:
                return extras
    return extras


def merge_tsquery_atoms(user_tokens: Sequence[str], extra: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for tok in list(user_tokens) + list(extra):
        t = (tok or "").strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


FORBIDDEN_MODULES: FrozenSet[str] = frozenset(
    {
        "auth",
        "tenant isolation",
        "RLS",
        "file ownership",
        "provider routing",
        "database schema",
    }
)
ALLOWED_MODULES: FrozenSet[str] = frozenset({"retrieval query preparation"})
