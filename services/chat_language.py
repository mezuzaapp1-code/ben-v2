"""Request-level chat response language (shared system prompt, not per-provider)."""
from __future__ import annotations

import re

from services.chat_prompt import GLOBAL_CHAT_SYSTEM

ALLOWED_LANGUAGE_CODES = frozenset({"en", "he"})
_LANGUAGE_NAMES = {"en": "English", "he": "Hebrew"}

DETECTION_MAX_CHARS = 8192
MIN_LETTERS = 8
DOMINANCE = 0.70
MIN_HEBREW_LETTERS = 2

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"\b[\w.-]+/[\w./-]+\b")
_LARGE_PASTE_STUB_RE = re.compile(r"\[Large paste · [^\]]+\]")
_BLOCKQUOTE_RE = re.compile(r"(?m)^>\s?.*$")
_QUOTED_SPAN_RE = re.compile(r"[“”«»\"].{8,}?[“”«»\"]", re.DOTALL)

_HEBREW_LETTER_RE = re.compile(r"[\u0590-\u05FF]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
_USER_MESSAGE_RE = re.compile(r"<user_message>\s*([\s\S]*?)\s*</user_message>", re.I)

# (substring, language code) — longer Hebrew phrases listed before "בעברית"
_IN_MESSAGE_OVERRIDE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("respond in english", "en"),
    ("reply in english", "en"),
    ("answer in english", "en"),
    ("באנגלית", "en"),
    ("תענה בעברית", "he"),
    ("ענה בעברית", "he"),
    ("בעברית", "he"),
)

CURRENT_TURN_LANGUAGE_RULE = (
    "Answer in the language of the user's current request. "
    "Hebrew request → Hebrew response. English request → English response. "
    "An explicit language request in this message overrides automatic matching. "
    "Quoted text, pasted source, file excerpts, visible image text, and prior "
    "conversation turns must not change the reply language."
)


def normalize_language_code(raw: str | None) -> str | None:
    """BCP-47-ish code for /chat; None when omitted."""
    if raw is None:
        return None
    code = str(raw).strip().lower()
    if not code:
        return None
    if code not in ALLOWED_LANGUAGE_CODES:
        raise ValueError("preferred_language must be one of: en, he")
    return code


def build_language_instruction(language_code: str) -> str:
    code = normalize_language_code(language_code)
    if not code:
        raise ValueError("language_code required")
    name = _LANGUAGE_NAMES[code]
    return (
        f"Language preference: Respond in {name} ({code}).\n"
        f"Unless the user explicitly asks for a different language in this message, "
        f"use {name} for all natural-language prose.\n"
        "Do not translate code blocks, inline code, file paths, URLs, JSON, or identifiers."
    )


def instruction_surface_for_language(message: str) -> str:
    """Current-turn typed instruction only — never Large Paste/file/history."""
    from services.message_format import user_turn_instruction_text

    text = user_turn_instruction_text(message or "")
    tagged = _USER_MESSAGE_RE.search(text)
    if tagged:
        return tagged.group(1)
    return text


def extract_in_message_language_override(message: str) -> str | None:
    """Explicit language request in the current instruction; last match wins."""
    text = instruction_surface_for_language(message)[:DETECTION_MAX_CHARS].casefold()
    if not text.strip():
        return None
    best_end = -1
    best_code: str | None = None
    for phrase, code in _IN_MESSAGE_OVERRIDE_PATTERNS:
        start = 0
        while True:
            idx = text.find(phrase, start)
            if idx < 0:
                break
            end = idx + len(phrase)
            if end > best_end:
                best_end = end
                best_code = code
            start = idx + 1
    return best_code


def strip_code_regions_for_detection(message: str) -> str:
    """Remove code-like regions before script scoring (detection only)."""
    text = (message or "")[:DETECTION_MAX_CHARS]
    text = _FENCED_CODE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _PATH_RE.sub(" ", text)
    return text


def strip_non_instruction_for_detection(message: str) -> str:
    """Score only current-turn instruction; ignore quotes, paste stubs, and code."""
    sample = strip_code_regions_for_detection(instruction_surface_for_language(message))
    sample = _LARGE_PASTE_STUB_RE.sub(" ", sample)
    sample = _BLOCKQUOTE_RE.sub(" ", sample)
    sample = _QUOTED_SPAN_RE.sub(" ", sample)
    return sample


def detect_language_code(message: str) -> str | None:
    """Infer en/he from the current-turn instruction surface; None if unclear."""
    sample = strip_non_instruction_for_detection(message)
    he_score = len(_HEBREW_LETTER_RE.findall(sample))
    en_score = len(_LATIN_LETTER_RE.findall(sample))
    total = he_score + en_score
    if he_score >= MIN_HEBREW_LETTERS and en_score == 0:
        return "he"
    if total < MIN_LETTERS:
        return None
    he_ratio = he_score / total
    if he_ratio >= DOMINANCE:
        return "he"
    en_ratio = en_score / total
    if en_ratio >= DOMINANCE:
        return "en"
    return None


def resolve_response_language(
    message: str,
    preferred_language: str | None,
) -> str | None:
    """Precedence: in-message override → API preferred_language → auto-detect."""
    override = extract_in_message_language_override(message)
    if override:
        return override
    if preferred_language:
        return preferred_language
    return detect_language_code(message)


def assemble_chat_system(
    raw_user_message: str,
    preferred_language: str | None,
    *,
    base_system: str | None = None,
) -> str:
    """Shared system prompt for every speaking provider. Language lives here."""
    base = (base_system or GLOBAL_CHAT_SYSTEM).strip()
    parts = [base, CURRENT_TURN_LANGUAGE_RULE]
    code = resolve_response_language(raw_user_message, preferred_language)
    if code:
        parts.append(build_language_instruction(code))
    return "\n\n".join(parts)


def apply_language_context(raw_user_message: str, preferred_language: str | None) -> str:
    """Legacy user-payload wrap. Chat routes use assemble_chat_system instead."""
    code = resolve_response_language(raw_user_message, preferred_language)
    if not code:
        return raw_user_message
    return f"{build_language_instruction(code)}\n\n{raw_user_message}"
