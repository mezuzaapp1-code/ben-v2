"""Multi-layered project memory matrix — tenant-scoped via KnowledgeObject + RLS."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text

from database.connection import get_db_session
from database.models import KnowledgeObject, ProjectMember, ProjectTask

DEFAULT_BASE_LOCATION = "Or Akiva"
SUBSISTENCE_MIN_NIS = 65
SUBSISTENCE_MAX_NIS = 100
SUBSISTENCE_DEFAULT_NIS = 80

QUOTATION_STEPS = (
    ("location", "Location"),
    ("materials_suppliers", "Materials / Suppliers"),
    ("risk_mitigation", "Risk Mitigation"),
    ("labor_execution", "Labor / Execution"),
)

LIFECYCLE_PHASES = (
    "quote_initialization",
    "activation",
    "execution",
    "final_closure",
)

# Simulated round-trip logistics lookup from Or Akiva (km, one-way minutes).
_LOCATION_LOGISTICS: dict[str, dict[str, float]] = {
    "shoham": {"distance_km": 55, "one_way_min": 48, "fuel_nis_per_km": 0.85},
    "tel aviv": {"distance_km": 68, "one_way_min": 62, "fuel_nis_per_km": 0.85},
    "haifa": {"distance_km": 42, "one_way_min": 38, "fuel_nis_per_km": 0.85},
    "jerusalem": {"distance_km": 118, "one_way_min": 95, "fuel_nis_per_km": 0.9},
    "netanya": {"distance_km": 22, "one_way_min": 22, "fuel_nis_per_km": 0.8},
    "herzliya": {"distance_km": 58, "one_way_min": 52, "fuel_nis_per_km": 0.85},
}


def _memory_title(project_id: uuid.UUID) -> str:
    return f"ben:project_memory:{project_id}"


def _empty_matrix() -> dict[str, Any]:
    return {
        "base_location": DEFAULT_BASE_LOCATION,
        "lifecycle": {phase: None for phase in LIFECYCLE_PHASES},
        "quotation_flow": {
            "active": False,
            "current_step": None,
            "steps": {
                key: {"label": label, "completed": False, "data": {}}
                for key, label in QUOTATION_STEPS
            },
        },
        "location_logistics": {"targets": {}},
        "subsistence": {
            "daily_allowance_nis": SUBSISTENCE_DEFAULT_NIS,
            "active_members": 0,
            "task_days": 0,
            "total_overhead_nis": 0,
        },
        "estimates": {"total_cost_nis": None, "margin_pct": None, "actual_cost_nis": None},
        "site_intelligence": None,
        "member_compliance": {},
        "daily_operations": [],
        "last_daily_briefing": None,
        "tactical_quotation": None,
        "attendance_log": [],
        "shift_schedule": {"start": "07:00", "end": "17:00"},
        "procurement_tenders": [],
        "shopping_log": [],
        "skill_blueprint": [],
        "engineering_scope": None,
        "worker_certifications": {},
        "training_sessions": [],
        "certification_gaps": [],
        "last_training_roi": None,
        "basalt_applications": [],
        "basalt_storage": {"bucket": None, "objects": []},
        "key_contacts": "",
        "initial_tactical_tasks": "",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_location_key(name: str) -> str:
    return (name or "").strip().lower()


def compute_location_logistics(target_location: str) -> dict[str, Any]:
    key = _normalize_location_key(target_location)
    spec = _LOCATION_LOGISTICS.get(key)
    if not spec:
        distance_km = max(25, len(key) * 8)
        one_way_min = int(distance_km * 1.1)
        fuel_nis_per_km = 0.85
    else:
        distance_km = spec["distance_km"]
        one_way_min = int(spec["one_way_min"])
        fuel_nis_per_km = spec["fuel_nis_per_km"]
    round_trip_km = distance_km * 2
    round_trip_min = one_way_min * 2
    fuel_nis = round(round_trip_km * fuel_nis_per_km, 2)
    return {
        "base": DEFAULT_BASE_LOCATION,
        "target": target_location.strip(),
        "distance_km": distance_km,
        "round_trip_km": round_trip_km,
        "one_way_min": one_way_min,
        "round_trip_min": round_trip_min,
        "fuel_nis": fuel_nis,
        "traffic_buffer_min": int(round_trip_min * 0.15),
    }


async def _count_active_members(session, org_id: uuid.UUID, project_id: uuid.UUID) -> int:
    q = select(ProjectMember).where(
        ProjectMember.org_id == org_id,
        ProjectMember.project_id == project_id,
        ProjectMember.member_type == "EMPLOYEE",
    )
    return len((await session.execute(q)).scalars().all())


async def _estimate_task_days(session, org_id: uuid.UUID, project_id: uuid.UUID) -> int:
    q = select(ProjectTask).where(
        ProjectTask.org_id == org_id,
        ProjectTask.project_id == project_id,
        ProjectTask.status.in_(("todo", "in_progress", "blocked")),
    )
    tasks = (await session.execute(q)).scalars().all()
    if not tasks:
        return 1
    return max(1, min(30, len(tasks)))


def compute_subsistence_overhead(
    *,
    active_members: int,
    task_days: int,
    daily_allowance_nis: float | None = None,
) -> dict[str, Any]:
    allowance = daily_allowance_nis or SUBSISTENCE_DEFAULT_NIS
    allowance = max(SUBSISTENCE_MIN_NIS, min(SUBSISTENCE_MAX_NIS, allowance))
    members = max(1, active_members)
    days = max(1, task_days)
    total = round(allowance * members * days, 2)
    return {
        "daily_allowance_nis": allowance,
        "allowance_range_nis": [SUBSISTENCE_MIN_NIS, SUBSISTENCE_MAX_NIS],
        "active_members": members,
        "task_days": days,
        "total_overhead_nis": total,
    }


async def load_project_memory(org_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})
        title = _memory_title(project_id)
        q = select(KnowledgeObject).where(
            KnowledgeObject.org_id == org_id,
            KnowledgeObject.title == title,
        )
        row = (await session.execute(q)).scalar_one_or_none()
        if row is None:
            return _empty_matrix()
        content = row.content if isinstance(row.content, dict) else {}
        matrix = _empty_matrix()
        matrix.update(content.get("matrix") or content)
        return matrix


DEFAULT_SHIFT_HOURS = 10.0
DEFAULT_SUBSISTENCE_NIS = 80.0


async def initialize_project_setup(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    location_base: str | None = None,
    key_contacts: str | None = None,
    initial_tactical_tasks: str | None = None,
) -> dict[str, Any]:
    """Seed tenant-isolated project memory with ops defaults (shift/subsistence hardcoded for setup)."""
    matrix = _empty_matrix()
    base = (location_base or DEFAULT_BASE_LOCATION).strip()[:256] or DEFAULT_BASE_LOCATION
    hours = DEFAULT_SHIFT_HOURS
    start_h = 7
    end_h = min(23, start_h + int(round(hours)))
    allowance = DEFAULT_SUBSISTENCE_NIS
    allowance = max(SUBSISTENCE_MIN_NIS, min(SUBSISTENCE_MAX_NIS, allowance))

    matrix["base_location"] = base
    matrix["shift_schedule"] = {"start": "07:00", "end": f"{end_h:02d}:00"}
    matrix["subsistence"] = {
        **matrix["subsistence"],
        "daily_allowance_nis": allowance,
        "allowance_range_nis": [SUBSISTENCE_MIN_NIS, SUBSISTENCE_MAX_NIS],
    }
    matrix["key_contacts"] = (key_contacts or "").strip()[:8000]
    matrix["initial_tactical_tasks"] = (initial_tactical_tasks or "").strip()[:8000]
    return await save_project_memory(org_id, project_id, matrix)


async def save_project_memory(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    matrix: dict[str, Any],
) -> dict[str, Any]:
    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})
        title = _memory_title(project_id)
        q = select(KnowledgeObject).where(
            KnowledgeObject.org_id == org_id,
            KnowledgeObject.title == title,
        )
        row = (await session.execute(q)).scalar_one_or_none()
        payload = {"project_id": str(project_id), "matrix": matrix}
        if row is None:
            row = KnowledgeObject(
                org_id=org_id,
                type="insight",
                title=title,
                content=payload,
                status="active",
            )
            session.add(row)
        else:
            row.content = payload
            row.status = "active"
        await session.commit()
    return matrix


async def touch_lifecycle_phase(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    phase: str,
) -> dict[str, Any]:
    if phase not in LIFECYCLE_PHASES:
        raise ValueError(f"Unknown lifecycle phase: {phase}")
    matrix = await load_project_memory(org_id, project_id)
    lifecycle = matrix.setdefault("lifecycle", {})
    if lifecycle.get(phase) is None:
        lifecycle[phase] = _now_iso()
    await save_project_memory(org_id, project_id, matrix)
    return matrix


async def refresh_subsistence_in_matrix(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    task_days: int | None = None,
) -> dict[str, Any]:
    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})
        members = await _count_active_members(session, org_id, project_id)
        days = task_days if task_days is not None else await _estimate_task_days(session, org_id, project_id)
    matrix = await load_project_memory(org_id, project_id)
    matrix["subsistence"] = compute_subsistence_overhead(active_members=members, task_days=days)
    await save_project_memory(org_id, project_id, matrix)
    return matrix


def lifecycle_analytics(matrix: dict[str, Any]) -> dict[str, Any]:
    lifecycle = matrix.get("lifecycle") or {}
    start = lifecycle.get("quote_initialization")
    end = lifecycle.get("final_closure")
    now = datetime.now(timezone.utc)
    days_elapsed = None
    if start:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = (
                datetime.fromisoformat(end.replace("Z", "+00:00"))
                if end
                else now
            )
            days_elapsed = max(0, (end_dt - start_dt).days)
        except ValueError:
            days_elapsed = None
    estimates = matrix.get("estimates") or {}
    estimated = estimates.get("total_cost_nis")
    actual = estimates.get("actual_cost_nis")
    variance_nis = None
    variance_pct = None
    if estimated is not None and actual is not None:
        variance_nis = round(float(actual) - float(estimated), 2)
        if estimated:
            variance_pct = round((variance_nis / float(estimated)) * 100, 2)
    return {
        "lifecycle": lifecycle,
        "days_elapsed": days_elapsed,
        "estimated_cost_nis": estimated,
        "actual_cost_nis": actual,
        "variance_nis": variance_nis,
        "variance_pct": variance_pct,
    }
