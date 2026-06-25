"""Upskilling simulations, modular role requirements, and training-day ROI logic."""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from typing import Any

STATUTORY_ASSET = "statutory_asset"
TRAINABLE_ORIENTATION = "trainable_orientation"

# Modular skill catalog — statutory vs up-trainable.
_SKILL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "licensed_electrician",
        "label": "Licensed Electrician",
        "category": STATUTORY_ASSET,
        "regulatory_ref": "Ministry of Economy — electrician license",
        "scope_keywords": ("electric", "electrical", "wiring", "panel", "voltage"),
    },
    {
        "id": "crane_operator_license",
        "label": "Crane Operator License",
        "category": STATUTORY_ASSET,
        "regulatory_ref": "MoL crane operator permit",
        "scope_keywords": ("crane", "lifting", "hoist"),
    },
    {
        "id": "classified_site_orientation",
        "label": "Classified Site Orientation",
        "category": TRAINABLE_ORIENTATION,
        "regulatory_ref": "Project-specific safety induction",
        "scope_keywords": ("site", "orientation", "induction", "construction"),
    },
    {
        "id": "certified_height_work",
        "label": "Certified Height Work",
        "category": TRAINABLE_ORIENTATION,
        "regulatory_ref": "Fall protection / work-at-height",
        "scope_keywords": ("height", "scaffold", "scaffolding", "roof", "elevated"),
    },
    {
        "id": "welding_safety_layer",
        "label": "Welding Safety Layer",
        "category": TRAINABLE_ORIENTATION,
        "regulatory_ref": "Hot-work and welding PPE protocol",
        "scope_keywords": ("weld", "welding", "hot work", "hot-work"),
    },
    {
        "id": "confined_space_entry",
        "label": "Confined Space Entry",
        "category": TRAINABLE_ORIENTATION,
        "regulatory_ref": "Confined space attendant certification",
        "scope_keywords": ("confined", "tank", "tunnel", "shaft"),
    },
]

_DEFAULT_ONSITE_PROCTOR_DAY_NIS = 4500.0
_DEFAULT_OFFSITE_PER_WORKER_NIS = 850.0
_DEFAULT_TRANSIT_PER_WORKER_NIS = 120.0
_DEFAULT_DELAY_DAY_COST_NIS = 650.0


def _normalize_scope(scope: str) -> str:
    return (scope or "").strip().lower()


def derive_job_requirements(engineering_scope: str) -> dict[str, Any]:
    """Break engineering scope into statutory prerequisites vs trainable orientations."""
    scope = _normalize_scope(engineering_scope)
    if not scope:
        scope = "general construction site execution"

    statutory: list[dict[str, Any]] = []
    trainable: list[dict[str, Any]] = []

    for skill in _SKILL_CATALOG:
        if any(kw in scope for kw in skill["scope_keywords"]):
            entry = {
                "skill_id": skill["id"],
                "label": skill["label"],
                "category": skill["category"],
                "regulatory_ref": skill["regulatory_ref"],
                "editable": skill["category"] == TRAINABLE_ORIENTATION,
            }
            if skill["category"] == STATUTORY_ASSET:
                statutory.append(entry)
            else:
                trainable.append(entry)

    if not statutory and not trainable:
        trainable = [
            {
                "skill_id": "classified_site_orientation",
                "label": "Classified Site Orientation",
                "category": TRAINABLE_ORIENTATION,
                "regulatory_ref": "Project-specific safety induction",
                "editable": True,
            }
        ]

    return {
        "engineering_scope": engineering_scope.strip()[:4000] if engineering_scope else scope,
        "statutory_assets": statutory,
        "trainable_orientations": trainable,
        "skill_blueprint": statutory + trainable,
        "statutory_count": len(statutory),
        "trainable_count": len(trainable),
    }


def _cert_status_for_worker(
    worker_name: str,
    skill_id: str,
    member_compliance: dict[str, Any],
    cert_registry: dict[str, list[dict[str, Any]]],
) -> str:
    """Return valid | missing | expired for a worker/skill pair."""
    profile = member_compliance.get(worker_name) or {}
    if profile.get("blocked") or profile.get("red_flags"):
        return "missing"

    certs = cert_registry.get(worker_name) or []
    for c in certs:
        if c.get("skill_id") == skill_id:
            status = (c.get("status") or "").lower()
            if status == "expired":
                return "expired"
            if status == "valid":
                return "valid"
    return "missing"


def scan_certification_gaps(
    *,
    skill_blueprint: list[dict[str, Any]],
    member_compliance: dict[str, Any],
    cert_registry: dict[str, list[dict[str, Any]]],
    project_members: list[str] | None = None,
) -> list[dict[str, Any]]:
    workers = project_members or list(member_compliance.keys()) or ["Field Worker"]
    gaps: list[dict[str, Any]] = []

    for worker in workers:
        for skill in skill_blueprint:
            sid = skill["skill_id"]
            status = _cert_status_for_worker(worker, sid, member_compliance, cert_registry)
            if status in ("missing", "expired"):
                gaps.append(
                    {
                        "worker_name": worker,
                        "skill_id": sid,
                        "skill_label": skill["label"],
                        "category": skill["category"],
                        "cert_status": status,
                    }
                )
    return gaps


def simulate_training_roi(
    *,
    gaps: list[dict[str, Any]],
    transit_per_worker_nis: float,
    onsite_proctor_day_nis: float = _DEFAULT_ONSITE_PROCTOR_DAY_NIS,
    offsite_per_worker_nis: float = _DEFAULT_OFFSITE_PER_WORKER_NIS,
) -> dict[str, Any]:
    """Compare onsite proctor day vs individual offsite training paths."""
    affected_workers = sorted({g["worker_name"] for g in gaps})
    gap_count = len(gaps)
    worker_count = len(affected_workers)

    onsite_transit = round(transit_per_worker_nis * max(1, worker_count // 2), 2)
    onsite_total = round(onsite_proctor_day_nis + onsite_transit, 2)
    offsite_total = round(
        (offsite_per_worker_nis + transit_per_worker_nis) * max(1, worker_count),
        2,
    )

    recommended = "onsite_proctor" if onsite_total <= offsite_total else "offsite_individual"
    savings_nis = round(abs(offsite_total - onsite_total), 2)

    delay_risk_nis = round(_DEFAULT_DELAY_DAY_COST_NIS * worker_count, 2)
    margin_impact_if_untrained = delay_risk_nis
    margin_impact_after_training = round(
        (onsite_total if recommended == "onsite_proctor" else offsite_total) - delay_risk_nis * 0.6,
        2,
    )

    return {
        "affected_worker_count": worker_count,
        "certification_gap_count": gap_count,
        "affected_workers": affected_workers,
        "onsite_proctor": {
            "proctor_day_nis": onsite_proctor_day_nis,
            "transit_overhead_nis": onsite_transit,
            "total_nis": onsite_total,
        },
        "offsite_individual": {
            "per_worker_nis": offsite_per_worker_nis,
            "transit_per_worker_nis": transit_per_worker_nis,
            "total_nis": offsite_total,
        },
        "recommended_path": recommended,
        "projected_savings_nis": savings_nis,
        "margin_impact": {
            "untrained_delay_risk_nis": margin_impact_if_untrained,
            "training_investment_nis": onsite_total if recommended == "onsite_proctor" else offsite_total,
            "net_after_training_nis": margin_impact_after_training,
        },
    }


def build_proctor_session(
    *,
    gaps: list[dict[str, Any]],
    roi: dict[str, Any],
    scheduled_date: str | None = None,
) -> dict[str, Any]:
    invitees = sorted({g["worker_name"] for g in gaps})
    skills = sorted({g["skill_label"] for g in gaps})
    session_date = scheduled_date or (date.today() + timedelta(days=7)).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "scheduled_date": session_date,
        "status": "scheduled",
        "invitation_list": invitees,
        "skills_covered": skills,
        "gap_count": len(gaps),
        "recommended_path": roi.get("recommended_path"),
        "projected_cost_nis": roi.get("onsite_proctor", {}).get("total_nis"),
        "margin_impact_nis": roi.get("margin_impact", {}).get("net_after_training_nis"),
    }
