"""Localized overload / runtime governance messages (en / he / ar)."""
from __future__ import annotations

import re
from typing import Any

COUNCIL_BUSY = "council_busy"
RUNTIME_SATURATED = "runtime_saturated"
RETRY_LATER = "retry_later"
DUPLICATE_REQUEST = "duplicate_request"

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")

_LABELS: dict[str, dict[str, dict[str, str]]] = {
    COUNCIL_BUSY: {
        "en": {
            "message": "Council is busy right now. Please wait a moment and try again.",
            "hint": "Another council session may still be running.",
        },
        "he": {
            "message": "המועצה עמוסה כרגע. המתן רגע ונסה שוב.",
            "hint": "ייתכן שמועצה אחרת עדיין פעילה.",
        },
        "ar": {
            "message": "المجلس مشغول حالياً. انتظر لحظة وحاول مرة أخرى.",
            "hint": "قد تكون جلسة مجلس أخرى لا تزال قيد التشغيل.",
        },
    },
    RUNTIME_SATURATED: {
        "en": {
            "message": "BEN is handling many requests. Please try again shortly.",
            "hint": "The runtime is temporarily at capacity.",
        },
        "he": {
            "message": "BEN מטפל כעת בכמות גדולה של בקשות. נסה שוב בעוד רגע.",
            "hint": "זמנית הגענו לקיבולת הריצה.",
        },
        "ar": {
            "message": "يعالج BEN عدداً كبيراً من الطلبات. حاول مرة أخرى بعد قليل.",
            "hint": "البيئة التشغيلية ممتلئة مؤقتاً.",
        },
    },
    RETRY_LATER: {
        "en": {
            "message": "Please try again in a few seconds.",
            "hint": "Runtime load is elevated; your request was not queued.",
        },
        "he": {
            "message": "נסה שוב בעוד מספר שניות.",
            "hint": "עומס הריצה גבוה; הבקשה לא נכנסה לתור.",
        },
        "ar": {
            "message": "حاول مرة أخرى بعد بضع ثوانٍ.",
            "hint": "الحمل مرتفع؛ لم تُوضَع طلبك في قائمة انتظار.",
        },
    },
    DUPLICATE_REQUEST: {
        "en": {
            "message": "This council request is already in progress.",
            "hint": "Wait for the current council to finish before submitting again.",
        },
        "he": {
            "message": "בקשת המועצה הזו כבר מתבצעת.",
            "hint": "המתן לסיום המועצה הנוכחית לפני שליחה חוזרת.",
        },
        "ar": {
            "message": "طلب المجلس هذا قيد التنفيذ بالفعل.",
            "hint": "انتظر حتى ينتهي المجلس الحالي قبل الإرسال مرة أخرى.",
        },
    },
}


def locale_from_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "en"
    he = len(_HEBREW_RE.findall(t))
    ar = len(_ARABIC_RE.findall(t))
    if he >= ar and he > 0:
        return "he"
    if ar > 0:
        return "ar"
    return "en"


def locale_from_accept_language(header: str | None) -> str | None:
    if not header:
        return None
    low = header.lower()
    if "he" in low or "iw" in low:
        return "he"
    if "ar" in low:
        return "ar"
    return None


def resolve_locale(*, accept_language: str | None = None, text: str = "") -> str:
    from_header = locale_from_accept_language(accept_language)
    if from_header:
        return from_header
    return locale_from_text(text)


def overload_detail(
    code: str,
    locale: str,
    *,
    retry_after_s: int = 5,
) -> dict[str, Any]:
    loc = locale if locale in ("he", "ar", "en") else "en"
    pack = _LABELS.get(code, _LABELS[RETRY_LATER])
    strings = pack.get(loc) or pack["en"]
    return {
        "code": code,
        "message": strings["message"],
        "hint": strings.get("hint", ""),
        "recoverable": True,
        "retry_after_s": retry_after_s,
    }
