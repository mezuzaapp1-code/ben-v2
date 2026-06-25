"""Tactical & government intelligence copilot tools (tenant-scoped, RLS-bound)."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException, status

from services.government_intelligence_service import lookup_site_intelligence, verify_member_compliance
from services.native_tools_service import add_project_member
from services.ops.request_context import attach_request_id
from services.project_copilot_tools import attach_mutated_state
from services.project_memory_service import (
    compute_location_logistics,
    compute_subsistence_overhead,
    load_project_memory,
    refresh_subsistence_in_matrix,
    save_project_memory,
    touch_lifecycle_phase,
)


async def fetch_site_intelligence(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    site_address: str | None = None,
    contractor_name: str | None = None,
) -> dict[str, Any]:
    if not (site_address or contractor_name):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "site_address or contractor_name is required",
        )

    intel = lookup_site_intelligence(site_address=site_address, contractor_name=contractor_name)
    matrix = await load_project_memory(org_id, project_id)
    matrix["site_intelligence"] = intel
    if site_address:
        logistics = compute_location_logistics(site_address)
        matrix.setdefault("location_logistics", {"targets": {}})["targets"][site_address] = logistics
        intel["logistics"] = logistics
    await save_project_memory(org_id, project_id, matrix)

    payload = {
        "tool": "fetch_site_intelligence",
        **intel,
    }
    return attach_request_id(attach_mutated_state("fetch_site_intelligence", payload))


async def initiate_tactical_quotation(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    site_address: str | None = None,
    contractor_name: str | None = None,
    base_quote_nis: float | None = None,
    crew_size: int | None = None,
    duration_days: int | None = None,
) -> dict[str, Any]:
    """Tactical quotation with government intelligence, hazard mapping, logistics, and subsistence."""
    address = (site_address or "").strip()
    if not address:
        matrix = await load_project_memory(org_id, project_id)
        targets = matrix.get("location_logistics", {}).get("targets") or {}
        if targets:
            address = next(iter(targets.keys()))
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "site_address is required")

    intel = lookup_site_intelligence(site_address=address, contractor_name=contractor_name)
    logistics = compute_location_logistics(address)
    members = max(1, int(crew_size or 3))
    days = max(1, int(duration_days or 5))
    subsistence = compute_subsistence_overhead(active_members=members, task_days=days)
    base = float(base_quote_nis or 50000)
    premium_pct = float(intel.get("safety_premium_pct") or 0)
    safety_premium_nis = round(base * (premium_pct / 100), 2)
    fuel_nis = float(logistics.get("fuel_nis") or 0)
    total_nis = round(base + safety_premium_nis + subsistence["total_overhead_nis"] + fuel_nis, 2)

    matrix = await load_project_memory(org_id, project_id)
    matrix["site_intelligence"] = intel
    matrix.setdefault("location_logistics", {"targets": {}})["targets"][address] = logistics
    matrix["subsistence"] = subsistence
    matrix.setdefault("estimates", {})
    matrix["estimates"]["total_cost_nis"] = total_nis
    matrix["estimates"]["safety_premium_nis"] = safety_premium_nis
    matrix["estimates"]["hazard_map"] = intel.get("hazard_map") or []
    matrix["tactical_quotation"] = {
        "site_address": address,
        "base_quote_nis": base,
        "safety_premium_pct": premium_pct,
        "safety_premium_nis": safety_premium_nis,
        "fuel_overhead_nis": fuel_nis,
        "subsistence_overhead_nis": subsistence["total_overhead_nis"],
        "total_quote_nis": total_nis,
    }
    await save_project_memory(org_id, project_id, matrix)
    await touch_lifecycle_phase(org_id, project_id, "quote_initialization")
    await refresh_subsistence_in_matrix(org_id, project_id, task_days=days)

    payload = {
        "tool": "initiate_tactical_quotation",
        "site_address": address,
        "registry_source": intel.get("registry_source"),
        "registered_site_manager": intel.get("registered_site_manager"),
        "crane_status": intel.get("crane_status"),
        "red_flags": intel.get("red_flags") or [],
        "hazard_map": intel.get("hazard_map") or [],
        "safety_premium_pct": premium_pct,
        "safety_premium_nis": safety_premium_nis,
        "logistics": logistics,
        "subsistence": subsistence,
        "base_quote_nis": base,
        "total_quote_nis": total_nis,
        "fuel_overhead_nis": fuel_nis,
    }
    return attach_request_id(attach_mutated_state("initiate_tactical_quotation", payload))


async def onboard_project_member(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    name: str,
    member_type: str = "EMPLOYEE",
    role: str | None = None,
    hourly_rate: float | None = None,
    email: str | None = None,
    phone: str | None = None,
    insurance_policy_id: str | None = None,
    contract_valid_until: str | None = None,
    safety_profile_score: int | None = None,
) -> dict[str, Any]:
    compliance = verify_member_compliance(
        name=name,
        insurance_policy_id=insurance_policy_id,
        contract_valid_until=contract_valid_until,
        safety_profile_score=safety_profile_score,
    )

    matrix = await load_project_memory(org_id, project_id)
    profiles = matrix.setdefault("member_compliance", {})
    profiles[name.strip()] = compliance

    member_record = None
    assignment_blocked = compliance["blocked"]

    if not assignment_blocked:
        member_record = await add_project_member(
            org_id,
            project_id,
            name=name,
            member_type=member_type,
            role=role,
            hourly_rate=hourly_rate,
            email=email,
            phone=phone,
            contact_notes=f"Insurance: {insurance_policy_id or 'n/a'}; Contract until: {contract_valid_until or 'n/a'}",
        )
        compliance["member_id"] = member_record.get("id")

    await save_project_memory(org_id, project_id, matrix)

    payload = {
        "tool": "onboard_project_member",
        "compliance_valid": compliance["compliance_valid"],
        "blocked": assignment_blocked,
        "worker_name": compliance["worker_name"],
        "issues": compliance["issues"],
        "red_flags": compliance["red_flags"],
        "insurance_policy_id": compliance["insurance_policy_id"],
        "contract_valid_until": compliance["contract_valid_until"],
        "safety_profile_score": compliance["safety_profile_score"],
        "member": member_record,
        "message": (
            "Assignment blocked — resolve compliance issues before assigning worker."
            if assignment_blocked
            else "Worker onboarded and compliance verified."
        ),
    }
    return attach_request_id(attach_mutated_state("onboard_project_member", payload))


async def log_daily_operations(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    clocked_hours: float | None = None,
    friction_events: list[str] | None = None,
    next_day_materials: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    matrix = await load_project_memory(org_id, project_id)
    ops_log = matrix.setdefault("daily_operations", [])
    today = date.today().isoformat()
    intel = matrix.get("site_intelligence") or {}
    logistics_targets = matrix.get("location_logistics", {}).get("targets") or {}
    fuel_total = sum(float(v.get("fuel_nis") or 0) for v in logistics_targets.values())
    subsistence = matrix.get("subsistence") or {}

    entry = {
        "date": today,
        "clocked_hours": float(clocked_hours or 8),
        "friction_events": list(friction_events or []),
        "next_day_materials": list(next_day_materials or []),
        "notes": (notes or "").strip() or None,
    }
    ops_log.append(entry)
    matrix["daily_operations"] = ops_log[-30:]

    priorities: list[str] = []
    if entry["next_day_materials"]:
        priorities.append(f"Procure materials: {', '.join(entry['next_day_materials'][:5])}")
    for flag in intel.get("red_flags") or []:
        priorities.append(f"Address safety flag: {flag.replace('_', ' ')}")
    for order in intel.get("active_safety_orders") or []:
        if order.get("status") == "open":
            priorities.append(f"Close safety order {order.get('id')}: {order.get('type')}")
    if entry["friction_events"]:
        priorities.append(f"Resolve friction: {entry['friction_events'][0]}")
    if not priorities:
        priorities.append("Continue scheduled execution per project plan")

    briefing = {
        "date": today,
        "next_day": (date.today() + timedelta(days=1)).isoformat(),
        "clocked_hours_today": entry["clocked_hours"],
        "friction_events": entry["friction_events"],
        "fuel_overhead_nis": fuel_total,
        "subsistence_overhead_nis": subsistence.get("total_overhead_nis"),
        "priorities": priorities[:6],
        "site_manager": intel.get("registered_site_manager"),
        "crane_status": intel.get("crane_status"),
        "active_red_flags": intel.get("red_flags") or [],
    }
    matrix["last_daily_briefing"] = briefing
    await save_project_memory(org_id, project_id, matrix)
    await touch_lifecycle_phase(org_id, project_id, "execution")

    payload = {
        "tool": "log_daily_operations",
        "daily_log": entry,
        "briefing": briefing,
    }
    return attach_request_id(attach_mutated_state("log_daily_operations", payload))
