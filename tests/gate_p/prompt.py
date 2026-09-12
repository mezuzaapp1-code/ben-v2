"""Neutral answer contract shared by every live provider mode."""
from __future__ import annotations

import json
import re
from typing import Any

ANSWER_CONTRACT = """Answer using only the attached document.

Return JSON only, with this shape:
{"answer":"...","answerable":true,"supporting_evidence":[{"text":"...","page":1}],"page":1}

Rules:
- If the document establishes the fact, set answerable to true and put the fact in answer.
- If the document does not establish the requested fact, set answerable to false and set answer to "not established".
- supporting_evidence must quote short passages from the document. Include page when the document shows a page number.
- Do not guess. Do not use outside knowledge.
"""


def user_prompt(question: str) -> str:
    return f"{ANSWER_CONTRACT}\nQuestion: {question}"


def parse_answer_payload(text: str | None) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {"answer": "", "answerable": None, "supporting_evidence": [], "page": None, "parse_ok": False}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return _normalize(data, raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return _normalize(data, raw)
        except json.JSONDecodeError:
            pass
    return {
        "answer": raw,
        "answerable": None,
        "supporting_evidence": [],
        "page": None,
        "parse_ok": False,
    }


def _normalize(data: dict[str, Any], raw: str) -> dict[str, Any]:
    page = data.get("page")
    if isinstance(page, str) and page.isdigit():
        page = int(page)
    if not isinstance(page, int):
        page = None
    evidence = data.get("supporting_evidence")
    if not isinstance(evidence, list):
        evidence = []
    answerable = data.get("answerable")
    if not isinstance(answerable, bool):
        answerable = None
    return {
        "answer": str(data.get("answer") or ""),
        "answerable": answerable,
        "supporting_evidence": evidence,
        "page": page,
        "parse_ok": True,
        "raw": raw[:4000],
    }
