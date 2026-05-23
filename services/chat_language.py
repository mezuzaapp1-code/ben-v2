"""Request-level chat response language preference (English instructions only)."""
from __future__ import annotations

import re

ALLOWED_LANGUAGE_CODES = frozenset({"en", "he"})
_LANGUAGE_NAMES = {"en": "English", "he": "Hebrew"}

DETECTION_MAX_CHARS = 8192
MIN_LETTERS = 8
DOMINANCE = 0.70

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"\b[\w.-]+/[\w./-]+\b")

_HEBREW_LETTER_RE = re.compile(r"[\u0590-\u05FF]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")

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


def extract_in_message_language_override(message: str) -> str | None:
    """Explicit language request in the user message; last match wins."""
    text = (message or "")[:DETECTION_MAX_CHARS].casefold()
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


def detect_language_code(message: str) -> str | None:
    """Infer en/he from dominant natural-language script; None if unclear."""
    sample = strip_code_regions_for_detection(message)
    he_score = len(_HEBREW_LETTER_RE.findall(sample))
    en_score = len(_LATIN_LETTER_RE.findall(sample))
    total = he_score + en_score
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


def apply_language_context(raw_user_message: str, preferred_language: str | None) -> str:
    """Wrap user text for provider gateway; persist raw_user_message separately."""
    code = resolve_response_language(raw_user_message, preferred_language)
    if not code:
        return raw_user_message
    return f"{build_language_instruction(code)}\n\n{raw_user_message}"
