"""Text normalization for deterministic classification."""
from __future__ import annotations

import re
from typing import Any, Iterable

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s/+.-]", re.UNICODE)


def normalize_text(value: str | None) -> str:
    text = (value or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def package_classification_text(package: dict[str, Any] | Any) -> str:
    """Concatenate headline, summary, and article titles in stable order."""
    if hasattr(package, "headline"):
        headline = str(getattr(package, "headline") or "")
        summary = str(getattr(package, "summary") or "")
        articles = list(getattr(package, "articles") or [])
        titles = [str(getattr(a, "title", "") or "") for a in articles]
    else:
        data = package or {}
        headline = str(data.get("headline") or "")
        summary = str(data.get("summary") or "")
        titles = [
            str(a.get("title") or "")
            for a in (data.get("articles") or [])
            if isinstance(a, dict)
        ]
    parts = [headline, summary, *sorted(titles)]
    return normalize_text(" \n ".join(p for p in parts if p and str(p).strip()))


def stable_join(parts: Iterable[str]) -> str:
    return "\n".join(parts)
