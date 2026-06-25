"""US Enterprise corporate copywriting and EHS schema for WWW.BASALT.CO.IL."""
from __future__ import annotations

from typing import Any

DEFAULT_LANG = "en"
SUPPORTED_LANGS = frozenset({"en", "he"})

_HERO_EN = (
    "BASALT | Data Center Infrastructure & Mission-Critical Human Capital. "
    "Engineering stability. Deploying precision. We deliver high-stakes execution "
    "and elite, certified workforces for next-generation data centers."
)

_HERO_HE = (
    "בזלת | תשתיות דאטה סנטר והון אנושי למערכות קריטיות. מהנדסים יציבות. פורסים דיוק. "
    "אנחנו מספקים ביצוע בסטנדרטים הגבוהים ביותר וכוח אדם עלית מוסמך עבור חוות השרתים של המחר."
)

_EHS_EN = (
    "Zero Friction. 100% EHS Compliance. Automated zero-trust gate controls verify "
    "active worker insurance and live background checks against Ministry of Labor registries."
)

_EHS_HE = (
    "אפס חיכוך. 100% ציות EHS. בקרות שער אוטומטיות Zero-Trust מאמתות ביטוח עובד פעיל "
    "ובדיקות רקע חיות מול רישומי משרד העבודה."
)

_COST_EN = (
    "All project bids enforce strict Cost Engineering principles: material net, "
    "Or Akiva logistics and freight overhead, and labor subsistence allocations "
    "are tabulated as discrete layers before commitment."
)

_COST_HE = (
    "כל הצעות המחיר מיישמות עקרונות הנדסת עלויות מחמירים: חומר נטו, לוגיסטיקה ושילוח מאור עקיבא, "
    "ועלויות סבסוד עבודה מפורקות כשכבות נפרדות לפני התחייבות."
)


def normalize_lang(lang: str | None) -> str:
    code = (lang or DEFAULT_LANG).strip().lower()[:2]
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


def build_corporate_content(lang: str | None = None) -> dict[str, Any]:
    """Multi-lingual corporate landing schema (US English default, Hebrew optional)."""
    code = normalize_lang(lang)
    return {
        "lang": code,
        "brand": "BASALT",
        "domain": "www.basalt.co.il",
        "vertical": "Data Center Infrastructure & Mission-Critical Human Capital",
        "hero": _HERO_HE if code == "he" else _HERO_EN,
        "hero_en": _HERO_EN,
        "hero_he": _HERO_HE,
        "ehs_compliance": {
            "headline": "Zero Friction. 100% EHS Compliance." if code == "en" else "אפס חיכוך. 100% ציות EHS.",
            "body": _EHS_HE if code == "he" else _EHS_EN,
            "zero_trust_gate": True,
            "mol_registry_verification": True,
        },
        "cost_engineering": {
            "headline": "Cost Engineering Focus" if code == "en" else "הנדסת עלויות",
            "body": _COST_HE if code == "he" else _COST_EN,
            "layers": [
                "base_material_cost",
                "logistics_freight_overhead",
                "operational_overheads",
                "labor_subsistence",
            ],
            "home_base": "Or Akiva",
        },
        "services": [
            "Mission-critical data center fit-out",
            "Certified technical workforce provisioning",
            "Classified zone electrical and height safety crews",
            "Live portfolio from verified financial ledger milestones",
        ],
    }
