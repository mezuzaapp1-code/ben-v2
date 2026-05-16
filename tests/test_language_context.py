"""Dominant language detection and cognitive label contracts."""
from __future__ import annotations

import pytest

from services.language_context import (
    LanguageContext,
    degraded_expert_message,
    detect_dominant_language,
    is_meaningful_reasoning_value,
    label,
)
from services.message_format import build_synthesis_display_text, prune_empty_synthesis_fields


def test_empty_fallback_english_ltr():
    ctx = detect_dominant_language("")
    assert ctx.dominant_language == "en"
    assert ctx.text_direction == "ltr"


def test_hebrew_dominant_rtl():
    ctx = detect_dominant_language("מה דעתך על הסכם זה עם הספק?")
    assert ctx.dominant_language == "he"
    assert ctx.text_direction == "rtl"


def test_english_dominant_ltr():
    ctx = detect_dominant_language("Should we launch Q2 product in the US market?")
    assert ctx.dominant_language == "en"
    assert ctx.text_direction == "ltr"


def test_arabic_dominant_rtl():
    ctx = detect_dominant_language("ما رأيك في هذا العقد مع المورد؟")
    assert ctx.dominant_language == "ar"
    assert ctx.text_direction == "rtl"


def test_mixed_hebrew_english_follows_dominant():
    ctx = detect_dominant_language("שלום לצוות — נא לעבור על החוזה עד יום שישי team")
    assert ctx.dominant_language in ("he", "mixed")
    assert ctx.text_direction in ("rtl", "ltr")


def test_deterministic_same_input():
    q = "בדיקת עקביות שפה"
    assert detect_dominant_language(q) == detect_dominant_language(q)


def test_hebrew_degraded_expert_message():
    ctx = LanguageContext("he", "rtl")
    msg = degraded_expert_message(ctx, "timeout")
    assert "פסק זמן" in msg
    assert "Expert unavailable" not in msg


def test_hebrew_synthesis_display_labels():
    ctx = LanguageContext("he", "rtl")
    text = build_synthesis_display_text(
        {
            "recommendation": "המלצה",
            "consensus_points": "הסכמה",
            "main_disagreement": None,
            "agreement_estimate": "2/2 available",
        },
        any_expert_failed=True,
        lang_ctx=ctx,
    )
    assert "קונצנזוס" in text
    assert "מחלוקת" in text
    assert "תגובות המומחים" in text


def test_empty_reasoning_fields_pruned():
    syn = {
        "recommendation": "ok",
        "consensus_points": "x",
        "legal_reasoning": "",
        "operational_reasoning": None,
        "strategic_reasoning": "null",
        "minority_or_unique_views": "   ",
    }
    out = prune_empty_synthesis_fields(syn)
    assert "legal_reasoning" not in out
    assert "operational_reasoning" not in out
    assert "strategic_reasoning" not in out
    assert "minority_or_unique_views" not in out


@pytest.mark.parametrize(
    "val,expected",
    [
        (None, False),
        ("", False),
        ("null", False),
        ("real content", True),
    ],
)
def test_is_meaningful_reasoning_value(val, expected):
    assert is_meaningful_reasoning_value(val) is expected


def test_english_timeout_label():
    ctx = LanguageContext("en", "ltr")
    assert "retry" in label(ctx, "council_timeout").lower()
