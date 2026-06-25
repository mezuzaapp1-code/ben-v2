"""Simulated open-source government registry intelligence (Ministry of Labor / data.gov.il)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

REGISTRY_SOURCE = "data.gov.il / Ministry of Labor — Active Building Sites (simulated)"

# Deterministic simulated registry keyed by normalized site address.
_SITE_REGISTRY: dict[str, dict[str, Any]] = {
    "shoham": {
        "registered_site_manager": "Yossi Cohen",
        "manager_license": "MOH-IL-44821",
        "crane_status": "active_permit",
        "crane_permit_expiry": "2026-12-31",
        "active_safety_orders": [
            {"id": "SO-2025-1142", "type": "scaffolding_inspection", "status": "open", "severity": "medium"},
            {"id": "SO-2025-1188", "type": "fall_protection", "status": "complied", "severity": "low"},
        ],
        "enforcement_penalties": [
            {"date": "2025-09-14", "violation": "scaffolding_anchor", "amount_nis": 12500, "status": "paid"},
            {"date": "2024-11-02", "violation": "unsecured_ladder", "amount_nis": 4800, "status": "paid"},
        ],
        "shutdown_history": [],
        "red_flags": ["historical_scaffolding_violations"],
    },
    "herzliya": {
        "registered_site_manager": "Dana Levi",
        "manager_license": "MOH-IL-39210",
        "crane_status": "permit_pending",
        "crane_permit_expiry": None,
        "active_safety_orders": [
            {"id": "SO-2026-0201", "type": "crane_operation", "status": "open", "severity": "high"},
        ],
        "enforcement_penalties": [],
        "shutdown_history": [{"date": "2025-06-01", "reason": "crane_permit_lapse", "duration_days": 3}],
        "red_flags": ["crane_permit_pending", "recent_shutdown"],
    },
    "netanya": {
        "registered_site_manager": "Avi Mizrahi",
        "manager_license": "MOH-IL-51002",
        "crane_status": "no_crane",
        "crane_permit_expiry": None,
        "active_safety_orders": [],
        "enforcement_penalties": [],
        "shutdown_history": [],
        "red_flags": [],
    },
}


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _synthetic_site_profile(query: str) -> dict[str, Any]:
    """Generate deterministic registry profile for unknown addresses."""
    digest = hashlib.sha256(query.encode()).hexdigest()
    penalty_count = int(digest[:2], 16) % 3
    has_scaffold = int(digest[2:4], 16) % 5 == 0
    red_flags: list[str] = []
    penalties = []
    if penalty_count:
        penalties.append(
            {
                "date": "2025-03-10",
                "violation": "ppe_compliance",
                "amount_nis": 3200 + penalty_count * 500,
                "status": "open" if penalty_count > 1 else "paid",
            }
        )
    if has_scaffold:
        red_flags.append("historical_scaffolding_violations")
    return {
        "registered_site_manager": f"Registry Manager {digest[:4].upper()}",
        "manager_license": f"MOH-IL-{int(digest[4:10], 16) % 90000 + 10000}",
        "crane_status": "active_permit" if int(digest[6:8], 16) % 2 else "no_crane",
        "crane_permit_expiry": "2026-08-15" if int(digest[6:8], 16) % 2 else None,
        "active_safety_orders": [
            {
                "id": f"SO-2026-{digest[8:12]}",
                "type": "general_safety_audit",
                "status": "open",
                "severity": "low",
            }
        ]
        if int(digest[10:12], 16) % 3
        else [],
        "enforcement_penalties": penalties,
        "shutdown_history": [],
        "red_flags": red_flags,
    }


def _contractor_modifier(contractor_name: str) -> dict[str, Any]:
    if not contractor_name:
        return {}
    key = _normalize_key(contractor_name)
    digest = hashlib.sha256(key.encode()).hexdigest()
    risk_score = int(digest[:2], 16) % 100
    return {
        "contractor_name": contractor_name.strip(),
        "contractor_risk_score": risk_score,
        "contractor_flags": (
            ["elevated_enforcement_history"] if risk_score > 70 else []
        ),
    }


def lookup_site_intelligence(
    *,
    site_address: str | None = None,
    contractor_name: str | None = None,
) -> dict[str, Any]:
    """
    Simulate Ministry of Labor / data.gov.il registry lookup for active building sites.
    """
    query = (site_address or contractor_name or "unknown site").strip()
    key = _normalize_key(site_address or query)
    # Match partial keys (e.g. "shoham industrial zone" → shoham)
    profile = None
    matched_key = key
    for reg_key, reg_data in _SITE_REGISTRY.items():
        if reg_key in key or key in reg_key:
            profile = dict(reg_data)
            matched_key = reg_key
            break
    if profile is None:
        profile = _synthetic_site_profile(query)
        matched_key = key

    contractor = _contractor_modifier(contractor_name or "")
    red_flags = list(profile.get("red_flags") or [])
    red_flags.extend(contractor.get("contractor_flags") or [])

    safety_premium_pct = 0.0
    if "historical_scaffolding_violations" in red_flags:
        safety_premium_pct += 4.5
    if "crane_permit_pending" in red_flags or profile.get("crane_status") == "permit_pending":
        safety_premium_pct += 6.0
    if profile.get("shutdown_history"):
        safety_premium_pct += 3.0
    if contractor.get("contractor_risk_score", 0) > 70:
        safety_premium_pct += 2.5

    hazard_map = []
    for p in profile.get("enforcement_penalties") or []:
        if "scaffold" in (p.get("violation") or "").lower():
            hazard_map.append(
                {
                    "hazard": "scaffolding",
                    "history": p,
                    "mitigation": "mandatory_scaffold_inspection_before_mobilization",
                }
            )
    if "historical_scaffolding_violations" in red_flags and not hazard_map:
        hazard_map.append(
            {
                "hazard": "scaffolding",
                "history": {"violation": "historical_scaffolding_violations"},
                "mitigation": "increased_safety_supervision_and_premium",
            }
        )

    return {
        "registry_source": REGISTRY_SOURCE,
        "query": query,
        "matched_registry_key": matched_key,
        "site_address": site_address or query,
        "registered_site_manager": profile.get("registered_site_manager"),
        "manager_license": profile.get("manager_license"),
        "crane_status": profile.get("crane_status"),
        "crane_permit_expiry": profile.get("crane_permit_expiry"),
        "active_safety_orders": profile.get("active_safety_orders") or [],
        "enforcement_penalties": profile.get("enforcement_penalties") or [],
        "shutdown_history": profile.get("shutdown_history") or [],
        "red_flags": red_flags,
        "safety_premium_pct": round(safety_premium_pct, 2),
        "hazard_map": hazard_map,
        "contractor": contractor or None,
        "compliance_clear": len(red_flags) == 0 and not profile.get("shutdown_history"),
    }


def verify_member_compliance(
    *,
    name: str,
    insurance_policy_id: str | None = None,
    contract_valid_until: str | None = None,
    safety_profile_score: int | None = None,
) -> dict[str, Any]:
    """Verify worker insurance, contract, and safety profile for onboarding."""
    issues: list[str] = []
    red_flags: list[str] = []

    policy = (insurance_policy_id or "").strip()
    if not policy:
        issues.append("missing_insurance_policy")
        red_flags.append("no_active_insurance")
    elif policy.upper().startswith("EXP") or policy.endswith("0"):
        issues.append("expired_insurance_policy")
        red_flags.append("expired_insurance")

    contract = (contract_valid_until or "").strip()
    if not contract:
        issues.append("missing_contract_agreement")
        red_flags.append("no_signed_contract")
    elif contract < "2026-01-01":
        issues.append("outdated_contract")
        red_flags.append("contract_expired")

    score = safety_profile_score if safety_profile_score is not None else 75
    if score < 60:
        issues.append("invalid_safety_profile")
        red_flags.append("safety_profile_below_threshold")

    valid = len(issues) == 0
    return {
        "worker_name": name.strip(),
        "insurance_policy_id": policy or None,
        "contract_valid_until": contract or None,
        "safety_profile_score": score,
        "compliance_valid": valid,
        "blocked": not valid,
        "issues": issues,
        "red_flags": red_flags,
    }
