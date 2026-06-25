"""Tier 0 zero-token intent router for the Local Codebase Expert lane."""
from __future__ import annotations

import re
from dataclasses import dataclass

CODE_STRONG = re.compile(
    r"@codebase|`[^`]+\.(py|jsx?|tsx?|ts|sql|md)`|\b(route|endpoint|FastAPI|React|migration|refactor|bug in)\b",
    re.I,
)
NON_CODE_STRONG = re.compile(
    r"\b(marketing|growth strategy|pricing|brand|fundraising|go-to-market)\b",
    re.I,
)
_CLIENT_FORCE_MARKER = re.compile(r"@codebase", re.I)


@dataclass(frozen=True)
class CodeIntentDecision:
    activate: bool
    confidence: float
    reason: str


def evaluate_code_intent(question: str, force: bool = False) -> CodeIntentDecision:
    """Return whether the Local Codebase Expert lane should activate."""
    q = question.strip()
    if force or _CLIENT_FORCE_MARKER.search(q):
        return CodeIntentDecision(True, 1.0, "client_force")
    if NON_CODE_STRONG.search(q) and not CODE_STRONG.search(q):
        return CodeIntentDecision(False, 0.95, "non_code_lexicon")
    if CODE_STRONG.search(q):
        return CodeIntentDecision(True, 0.9, "code_pattern")
    return CodeIntentDecision(False, 0.5, "ambiguous")
