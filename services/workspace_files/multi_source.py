"""Deterministic Multi-Source Resolution V1.

Request-time only. Does not mutate source_state, Initial Read, or retrieval
internals. Conversation-bounded: a multi-source phrase never expands to the
whole workspace.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from services.workspace_files.thread_sources import (
    normalize_source_state,
    restriction_file_ids,
)

MODE_PASSTHROUGH = "passthrough"
MODE_MULTI = "multi"
MODE_CLARIFY = "clarify"

INTENT_LAST_TWO = "last_two"
INTENT_PREVIOUS_CURRENT = "previous_current"
INTENT_PAIR = "pair"
INTENT_UNCOUNTED = "uncounted_plural"

Mode = Literal["passthrough", "multi", "clarify"]
Intent = Literal["last_two", "previous_current", "pair", "uncounted_plural"]

_NIKUD_RE = re.compile(r"[\u0591-\u05C7]")
_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")

# Specificity order: last-two and previous+current before generic pair/plural.
_LAST_TWO_RE = re.compile(
    r"(?:\blast\s+two\b|שני האחרונים|שתי האחרונות|השניים האחרונים|השתיים האחרונות)"
)
_PREVIOUS_CURRENT_RE = re.compile(
    r"(?:"
    r"\bprevious\b.+\b(?:this one|this file|this proposal|the current|current one|this)\b"
    r"|הקודם והנוכחי|הקודמת והנוכחית|הקובץ הקודם|ההצעה הקודמת"
    r")"
)
_PAIR_RE = re.compile(
    r"(?:"
    r"\bboth\b"
    r"|\bthe two\b"
    r"|\btwo (?:files|proposals|documents)\b"
    r"|\bcompare the two\b"
    r"|\bcompare both\b"
    r"|\bsum both\b"
    r"|שתי ההצעות|שני הקבצים|שתי הקבצים|שני ההצעות"
    r"|(?<!\d)2 ההצעות|(?<!\d)2 הקבצים"
    r"|\bשניהם\b|\bשתיהן\b|\bשתיהם\b"
    r"|שתי\s+(?:ההצעות|הקבצים|המסמכים)"
    r"|שני\s+(?:הקבצים|המסמכים|ההצעות)"
    r")"
)
_UNCOUNTED_RE = re.compile(
    r"(?:"
    r"\bcompare them\b"
    r"|\bcompare these\b"
    r"|\bcompare the proposals\b"
    r"|\bthe proposals\b"
    r"|\bthe files\b"
    r"|\bההצעות\b"
    r"|\bהקבצים\b"
    r"|(?:files|proposals|documents).+\btogether\b"
    r"|\btogether\b.+(?:files|proposals|documents)"
    r")"
)

MULTI_SOURCE_GROUNDING_HINT = (
    "<source_grounding>\n"
    "Source-grounded facts for this comparison (amounts, quotes, clauses) "
    "must come from the current-turn workspace file evidence. "
    "Conversation history is background only and is not a substitute for a "
    "file that was not injected.\n"
    "</source_grounding>"
)

_CLARIFY_HE = (
    "לא הצלחתי לקבוע בוודאות לאילו קבצים התכוונת. "
    "ציין שמות, או אמור \"שתי ההצעות האחרונות\" / \"שני הקבצים האחרונים\"."
)
_CLARIFY_EN = (
    "I couldn't tell which files you mean. "
    'Name them, or say "the last two files" / "both proposals".'
)


@dataclass(frozen=True)
class SourceResolution:
    mode: Mode
    file_ids: tuple[str, ...]
    reason: str
    intent: str | None = None

    @property
    def is_multi(self) -> bool:
        return self.mode == MODE_MULTI

    @property
    def is_clarify(self) -> bool:
        return self.mode == MODE_CLARIFY


def normalize_query(text: str | None) -> str:
    cleaned = _NIKUD_RE.sub("", " ".join(str(text or "").split()))
    return cleaned.casefold()


def classify_multi_source_intent(user_query: str | None) -> Intent | None:
    """Bounded HE/EN vocabulary. None = ordinary single-source / no multi phrase."""
    q = normalize_query(user_query)
    if not q:
        return None
    if _LAST_TWO_RE.search(q):
        return INTENT_LAST_TWO
    if _PREVIOUS_CURRENT_RE.search(q):
        return INTENT_PREVIOUS_CURRENT
    if _PAIR_RE.search(q):
        return INTENT_PAIR
    if _UNCOUNTED_RE.search(q):
        return INTENT_UNCOUNTED
    return None


def resolve_turn_sources(
    user_query: str | None,
    source_state: dict[str, Any] | None,
    *,
    ready_ids: set[str] | None = None,
) -> SourceResolution:
    """Resolve this turn's retrieval allow-list from query + conversation state."""
    state = normalize_source_state(source_state)
    intent = classify_multi_source_intent(user_query)
    if intent is None:
        restriction = restriction_file_ids(state)
        return SourceResolution(
            mode=MODE_PASSTHROUGH,
            file_ids=tuple(restriction),
            reason="active_or_pending",
        )

    selected, reason = _select_for_intent(intent, state)
    if selected is None:
        return SourceResolution(
            mode=MODE_CLARIFY,
            file_ids=(),
            reason=reason,
            intent=intent,
        )
    if ready_ids is not None:
        ready = {str(i).strip() for i in ready_ids if str(i).strip()}
        selected = [fid for fid in selected if fid in ready]
    if len(selected) < 2:
        return SourceResolution(
            mode=MODE_CLARIFY,
            file_ids=(),
            reason="insufficient_ready_sources",
            intent=intent,
        )
    return SourceResolution(
        mode=MODE_MULTI,
        file_ids=tuple(selected),
        reason=reason,
        intent=intent,
    )


def restrict_arg_for_resolution(resolution: SourceResolution) -> list[str] | None:
    """Map a resolution to load_ready_files_context restrict_to_file_ids.

    None = unrestricted (no pending/active). [] = fail-closed empty.
    A multi-source list is never None.
    """
    if resolution.mode == MODE_CLARIFY:
        return []
    if resolution.mode == MODE_MULTI:
        return list(resolution.file_ids)
    return list(resolution.file_ids) if resolution.file_ids else None


def clarification_text(user_query: str | None) -> str:
    if _HEBREW_RE.search(str(user_query or "")):
        return _CLARIFY_HE
    return _CLARIFY_EN


def wrap_with_grounding_hint(message: str) -> str:
    body = (message or "").strip()
    if not body:
        return MULTI_SOURCE_GROUNDING_HINT
    return f"{MULTI_SOURCE_GROUNDING_HINT}\n\n{body}"


def _select_for_intent(
    intent: Intent, state: dict[str, Any]
) -> tuple[list[str] | None, str]:
    conversation = _uniq(state["conversation_file_ids"])
    pending = _uniq(state["pending_file_ids"])
    active = _uniq(state["active_file_ids"])
    recent = _uniq(state["recent_file_ids"])

    if intent == INTENT_LAST_TWO:
        if len(conversation) < 2:
            return None, "last_two_requires_two_conversation_files"
        return conversation[-2:], "last_two_conversation"

    if intent == INTENT_PREVIOUS_CURRENT:
        current = active or pending
        if not recent or len(current) != 1:
            return None, "previous_current_not_a_pair"
        return [recent[-1], current[0]], "previous_and_current"

    if intent == INTENT_PAIR:
        pool = _conversation_bounded_pool(conversation, pending, active, recent)
        if len(pool) >= 2:
            return pool[-2:], "pair_last_two_in_pool"
        if len(conversation) >= 2:
            return conversation[-2:], "pair_last_two_conversation"
        return None, "pair_requires_two_sources"

    # uncounted plural / compare them|these
    cohort = _uniq([*pending, *active])
    if len(cohort) >= 2:
        return cohort, "uncounted_active_cohort"
    pool = _conversation_bounded_pool(conversation, pending, active, recent)
    if len(pool) == 2:
        return pool, "uncounted_exact_pair"
    return None, "uncounted_ambiguous"


def _conversation_bounded_pool(
    conversation: list[str],
    pending: list[str],
    active: list[str],
    recent: list[str],
) -> list[str]:
    live = set(pending) | set(active) | set(recent)
    ordered = [fid for fid in conversation if fid in live]
    extra = [fid for fid in [*pending, *active, *recent] if fid not in set(ordered)]
    return _uniq([*ordered, *extra])


def _uniq(raw: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or ():
        fid = str(item or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        out.append(fid)
    return out
