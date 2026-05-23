"""Request-level chat response language preference (English instructions only)."""
from __future__ import annotations

ALLOWED_LANGUAGE_CODES = frozenset({"en", "he"})
_LANGUAGE_NAMES = {"en": "English", "he": "Hebrew"}


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


def apply_language_context(raw_user_message: str, preferred_language: str | None) -> str:
    """Wrap user text for provider gateway; persist raw_user_message separately."""
    code = preferred_language
    if not code:
        return raw_user_message
    return f"{build_language_instruction(code)}\n\n{raw_user_message}"
