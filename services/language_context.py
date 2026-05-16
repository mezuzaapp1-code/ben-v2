"""Request-local dominant language detection and cognitive labels (no external deps)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

DominantLanguage = Literal["en", "he", "ar", "mixed", "unknown"]
TextDirection = Literal["ltr", "rtl"]

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# Minimum share of counted letters to win; mixed when runner-up within this ratio of leader.
_MIXED_RATIO = 0.35


@dataclass(frozen=True)
class LanguageContext:
    dominant_language: DominantLanguage
    text_direction: TextDirection

    @property
    def language_name(self) -> str:
        return {
            "en": "English",
            "he": "Hebrew",
            "ar": "Arabic",
            "mixed": "the user's dominant language",
            "unknown": "English",
        }[self.dominant_language]

    @property
    def label_locale(self) -> str:
        """Locale key for UI strings; mixed/unknown fall back to English labels."""
        if self.dominant_language in ("he", "ar", "en"):
            return self.dominant_language
        return "en"


def _count_scripts(text: str) -> dict[str, int]:
    he = len(_HEBREW_RE.findall(text))
    ar = len(_ARABIC_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))
    return {"he": he, "ar": ar, "en": lat}


def detect_dominant_language(text: str) -> LanguageContext:
    """
    Deterministic dominant-language detection from user text.
    Safe fallback: English + LTR when empty or no script signal.
    """
    t = (text or "").strip()
    if not t:
        return LanguageContext("en", "ltr")

    counts = _count_scripts(t)
    total = counts["he"] + counts["ar"] + counts["en"]
    if total == 0:
        return LanguageContext("en", "ltr")

    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    top_lang, top_n = ranked[0]
    second_n = ranked[1][1] if len(ranked) > 1 else 0

    if top_n == 0:
        return LanguageContext("en", "ltr")

    if second_n > 0 and second_n >= top_n * _MIXED_RATIO:
        direction: TextDirection = "rtl" if (counts["he"] + counts["ar"]) >= counts["en"] else "ltr"
        return LanguageContext("mixed", direction)

    dominant: DominantLanguage
    if top_lang == "he":
        dominant = "he"
    elif top_lang == "ar":
        dominant = "ar"
    else:
        dominant = "en"

    direction = "rtl" if dominant in ("he", "ar") else "ltr"
    return LanguageContext(dominant, direction)


def _labels(locale: str) -> dict[str, str]:
    table: dict[str, dict[str, str]] = {
        "en": {
            "expert_unavailable": "Expert unavailable ({category})",
            "category_timeout": "timeout",
            "category_config": "configuration",
            "category_error": "error",
            "status_timeout": "Unavailable (timeout)",
            "status_degraded": "Partial: {category}",
            "status_error": "Partial: error",
            "synthesis_prefix_degraded": "Based on available expert responses.",
            "synthesis_title": "BEN Synthesis ({ae})",
            "consensus": "Consensus",
            "disagreement": "Disagreement",
            "disagreement_none": "None",
            "synthesis_footer": "This is a structured reasoning layer, not a final answer.",
            "council_timeout": "Council took longer than expected. Please retry or shorten the request.",
            "long_prompt_hint": "This is a complex request and may take longer.",
        },
        "he": {
            "expert_unavailable": "המומחה אינו זמין ({category})",
            "category_timeout": "פסק זמן",
            "category_config": "תצורה",
            "category_error": "שגיאה",
            "status_timeout": "לא זמין (פסק זמן)",
            "status_degraded": "חלקי: {category}",
            "status_error": "חלקי: שגיאה",
            "synthesis_prefix_degraded": "על בסיס תגובות המומחים הזמינות.",
            "synthesis_title": "סינתזת BEN ({ae})",
            "consensus": "קונצנזוס",
            "disagreement": "מחלוקת",
            "disagreement_none": "אין",
            "synthesis_footer": "שכבת נימוק מובנית — לא תשובה סופית.",
            "council_timeout": "המועצה לקחה יותר זמן מהצפוי. נסה שוב או קצר את הבקשה.",
            "long_prompt_hint": "בקשה מורכבת — עשויה לקחת יותר זמן.",
        },
        "ar": {
            "expert_unavailable": "الخبير غير متاح ({category})",
            "category_timeout": "انتهاء المهلة",
            "category_config": "الإعداد",
            "category_error": "خطأ",
            "status_timeout": "غير متاح (انتهاء المهلة)",
            "status_degraded": "جزئي: {category}",
            "status_error": "جزئي: خطأ",
            "synthesis_prefix_degraded": "بناءً على آراء الخبراء المتاحة.",
            "synthesis_title": "تركيب BEN ({ae})",
            "consensus": "الإجماع",
            "disagreement": "الخلاف",
            "disagreement_none": "لا يوجد",
            "synthesis_footer": "طبقة استدلال منظمة — وليست إجابة نهائية.",
            "council_timeout": "استغرق المجلس وقتاً أطول من المتوقع. أعد المحاولة أو اختصر الطلب.",
            "long_prompt_hint": "طلب معقد — قد يستغرق وقتاً أطول.",
        },
    }
    return table.get(locale, table["en"])


def label(ctx: LanguageContext, key: str, **fmt: Any) -> str:
    s = _labels(ctx.label_locale).get(key) or _labels("en")[key]
    return s.format(**fmt) if fmt else s


def degraded_expert_message(ctx: LanguageContext, category: str) -> str:
    if category == "timeout":
        cat_key = "category_timeout"
    elif category == "config_error":
        cat_key = "category_config"
    else:
        cat_key = "category_error"
    cat_label = label(ctx, cat_key)
    return label(ctx, "expert_unavailable", category=cat_label)


def expert_status_label(ctx: LanguageContext, outcome: str, response: str) -> str | None:
    if not outcome or outcome == "ok":
        return None
    if outcome == "timeout":
        return label(ctx, "status_timeout")
    m = re.search(r"Expert unavailable \(([^)]+)\)|המומחה אינו זמין \(([^)]+)\)|الخبير غير متاح \(([^)]+)\)", response or "")
    if m:
        cat = next(g for g in m.groups() if g)
        return label(ctx, "status_degraded", category=cat)
    if outcome == "degraded":
        return label(ctx, "status_degraded", category=label(ctx, "category_error"))
    if outcome == "error":
        return label(ctx, "status_error")
    return label(ctx, "status_degraded", category=outcome)


def synthesis_language_clause(ctx: LanguageContext) -> str:
    return (
        f"LANGUAGE CONTRACT (mandatory): The user's dominant language for this request is "
        f"{ctx.language_name} (code={ctx.dominant_language}). "
        f"Every string value in your JSON output MUST be written in that language only. "
        f"Do not switch to English unless the user's question is predominantly English. "
        f"agreement_estimate may use numeric forms like \"2/2 available\" or localized equivalent."
    )


def synthesis_system_prompt(ctx: LanguageContext) -> str:
    base = """You are BEN, a Cognitive Operating System.
Your job is to synthesize expert opinions into structured organizational reasoning.
Return ONLY valid JSON. No markdown. No explanations outside the JSON.

Rules — honesty (must always hold):
- Only count experts with outcome=ok as agreeing experts.
- Do not claim 3/3 or 2/3 style agreement if any expert timed out or was unavailable.
- If any expert failed, say in consensus_points that synthesis uses only available experts.
- agreement_estimate must reflect available experts only (e.g. "2/2 available", "3/3 available", "unknown").

Rules — reasoning preservation (must always hold):
- Preserve distinct expert lenses. Do NOT collapse Legal, Business/operational, and Strategy into one generic voice.
- If experts agree on a conclusion but differ on WHY, capture those WHY differences in disagreement_points and/or domain sections.
- agreement vs rationale: consensus_points = what aligns; disagreement_points = where they diverge (including rationale), without fabricating conflict.
- Use domain sections to hold domain-specific priorities and risk framing (legal vs operational vs strategic vs infrastructure where relevant).
- Be faithful: do not invent unanimous consensus when experts emphasized different risks or tradeoffs.
- minority_or_unique_views: views articulated by one expert or clearly not shared by others (or null).
- Omit optional keys or set them to null if not applicable."""
    return f"{base}\n\n{synthesis_language_clause(ctx)}"


def expert_respond_clause(ctx: LanguageContext) -> str:
    if ctx.dominant_language == "en":
        return ""
    return f"\nRespond only in {ctx.language_name}."


def chat_user_message_with_language(message: str, ctx: LanguageContext) -> str:
    if ctx.dominant_language == "en":
        return message
    return f"[Respond only in {ctx.language_name}]\n\n{message}"


def attach_language_metadata(payload: dict[str, Any], ctx: LanguageContext) -> dict[str, Any]:
    payload["dominant_language"] = ctx.dominant_language
    payload["text_direction"] = ctx.text_direction
    return payload


def is_meaningful_reasoning_value(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, (dict, list)):
        return bool(val)
    s = str(val).strip()
    if not s:
        return False
    if s.lower() in ("null", "none", "n/a", "na"):
        return False
    return True


def language_context_from_payload(payload: dict[str, Any]) -> LanguageContext:
    dl = payload.get("dominant_language") or "en"
    td = payload.get("text_direction") or "ltr"
    if dl not in ("en", "he", "ar", "mixed", "unknown"):
        dl = "en"
    if td not in ("ltr", "rtl"):
        td = "ltr"
    return LanguageContext(dl, td)
