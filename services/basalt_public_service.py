"""Basalt public corporate API — jobs, applications, portfolio (tenant-isolated)."""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from services.basalt_content_schema import build_corporate_content, normalize_lang
from services.project_memory_service import DEFAULT_BASE_LOCATION, load_project_memory, save_project_memory
from services.project_service import list_projects
from services.native_tools_service import list_ledger_entries, list_project_members
from services.upskilling_service import derive_job_requirements, scan_certification_gaps

BASALT_ORG_ENV = "BASALT_ORG_ID"
BASALT_PROJECT_ENV = "BASALT_DEFAULT_PROJECT_ID"
DEFAULT_BASALT_ORG = "22222222-2222-2222-2222-222222222222"

_SKILL_ROLE_MAP: dict[str, str] = {
    "weld": "Welder",
    "welder": "Welder",
    "welding": "Welder",
    "draft": "Draftsman",
    "draftsman": "Draftsman",
    "autocad": "Draftsman",
    "electric": "Licensed Electrician",
    "electrician": "Licensed Electrician",
    "crane": "Crane Operator",
    "height": "Height Safety Technician",
    "scaffold": "Scaffold Specialist",
    "hvac": "HVAC Technician",
    "data center": "Data Center Technician",
    "fiber": "Fiber Optic Technician",
}

_CERT_FILE_TYPES = frozenset({
    "height_safety",
    "electrical_credentials",
    "classified_zone",
    "welding_safety",
    "general_safety",
})


def resolve_basalt_org_id() -> uuid.UUID:
    """Authoritative org scope — never accept org_id from public clients."""
    raw = os.getenv(BASALT_ORG_ENV, DEFAULT_BASALT_ORG).strip()
    return uuid.UUID(raw)


def resolve_basalt_project_id() -> uuid.UUID | None:
    raw = os.getenv(BASALT_PROJECT_ENV, "").strip()
    if not raw:
        return None
    return uuid.UUID(raw)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage_key(org_id: uuid.UUID, application_id: str, filename: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", filename or "file")
    return f"{org_id}/applications/{application_id}/{safe}"


def parse_resume_skills(resume_text: str, certifications: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Extract skill matrix bounds from resume text and certification metadata."""
    text = (resume_text or "").lower()
    found: dict[str, dict[str, Any]] = {}

    for keyword, role in _SKILL_ROLE_MAP.items():
        if keyword in text and role not in found:
            found[role] = {"role": role, "source": "resume", "confidence": "inferred"}

    for cert in certifications or []:
        ctype = (cert.get("cert_type") or cert.get("type") or "").strip().lower()
        label = cert.get("label") or ctype.replace("_", " ").title()
        role = _SKILL_ROLE_MAP.get(ctype.split("_")[0], label)
        found[role] = {
            "role": role,
            "source": "certification",
            "cert_type": ctype,
            "filename": cert.get("filename"),
            "confidence": "verified_upload",
        }

    return list(found.values())


def _job_from_gaps(
    project_id: uuid.UUID,
    project_name: str,
    gaps: list[dict[str, Any]],
    lang: str,
) -> list[dict[str, Any]]:
    openings: list[dict[str, Any]] = []
    skills_needed: dict[str, int] = {}
    for g in gaps:
        label = g.get("skill_label") or g.get("skill_id")
        skills_needed[label] = skills_needed.get(label, 0) + 1

    for skill_label, deficit in skills_needed.items():
        title_en = f"{skill_label} — Data Center Infrastructure Crew"
        title_he = f"{skill_label} — צוות תשתיות דאטה סנטר"
        openings.append(
            {
                "id": str(uuid.uuid4()),
                "project_id": str(project_id),
                "project_name": project_name,
                "title": title_he if lang == "he" else title_en,
                "title_en": title_en,
                "title_he": title_he,
                "skill_label": skill_label,
                "labor_deficit": deficit,
                "category": next((g.get("category") for g in gaps if g.get("skill_label") == skill_label), None),
                "home_base": DEFAULT_BASE_LOCATION,
                "status": "active",
                "source": "ben_recruitment_tools",
            }
        )
    return openings


async def fetch_active_job_openings(org_id: uuid.UUID, *, lang: str | None = None) -> dict[str, Any]:
    """Dynamic job openings from labor deficits detected by BEN recruitment tools."""
    code = normalize_lang(lang)
    projects_data = await list_projects(org_id)
    projects = projects_data.get("projects") or []
    default_pid = resolve_basalt_project_id()
    if default_pid:
        projects = [p for p in projects if p.get("id") == str(default_pid)] or projects[:1]

    all_openings: list[dict[str, Any]] = []
    for proj in projects:
        pid = uuid.UUID(proj["id"])
        matrix = await load_project_memory(org_id, pid)
        blueprint = matrix.get("skill_blueprint") or []
        if not blueprint and matrix.get("engineering_scope"):
            blueprint = derive_job_requirements(matrix["engineering_scope"])["skill_blueprint"]
        if not blueprint:
            blueprint = derive_job_requirements(
                "data center electrical fit-out welding at height classified site"
            )["skill_blueprint"]

        members_data = await list_project_members(org_id, pid)
        names = [m.get("name") for m in (members_data.get("members") or []) if m.get("name")]
        gaps = matrix.get("certification_gaps") or scan_certification_gaps(
            skill_blueprint=blueprint,
            member_compliance=matrix.get("member_compliance") or {},
            cert_registry=matrix.get("worker_certifications") or {},
            project_members=names,
        )
        if gaps:
            all_openings.extend(_job_from_gaps(pid, proj.get("name") or "Project", gaps, code))

    if not all_openings:
        fallback = _job_from_gaps(
            default_pid or uuid.uuid4(),
            "Mission-Critical Data Center Program",
            [
                {
                    "skill_label": "Licensed Electrician",
                    "skill_id": "licensed_electrician",
                    "category": "statutory_asset",
                },
                {
                    "skill_label": "Certified Height Work",
                    "skill_id": "certified_height_work",
                    "category": "trainable_orientation",
                },
            ],
            code,
        )
        all_openings = fallback

    return {
        "org_id": str(org_id),
        "lang": code,
        "openings": all_openings,
        "opening_count": len(all_openings),
        "content": build_corporate_content(code),
    }


async def submit_candidate_application(
    org_id: uuid.UUID,
    *,
    candidate_name: str,
    email: str | None = None,
    phone: str | None = None,
    resume_text: str | None = None,
    desired_role: str | None = None,
    certifications: list[dict[str, Any]] | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    """Parse resume, store certification uploads in tenant bucket, PENDING_REVIEW state."""
    name = (candidate_name or "").strip()
    if not name:
        raise ValueError("candidate_name is required")

    project_id = resolve_basalt_project_id()
    if project_id is None:
        projects_data = await list_projects(org_id)
        projects = projects_data.get("projects") or []
        if not projects:
            raise ValueError("No Basalt project configured for applications")
        project_id = uuid.UUID(projects[0]["id"])

    application_id = str(uuid.uuid4())
    certs = certifications or []
    storage_files: list[dict[str, Any]] = []
    for cert in certs:
        ctype = (cert.get("cert_type") or cert.get("type") or "general_safety").strip().lower()
        if ctype not in _CERT_FILE_TYPES:
            ctype = "general_safety"
        filename = cert.get("filename") or f"{ctype}.pdf"
        storage_files.append(
            {
                "cert_type": ctype,
                "filename": filename,
                "storage_key": _storage_key(org_id, application_id, filename),
                "status": "stored",
            }
        )

    skill_matrix = parse_resume_skills(resume_text or "", storage_files)
    if desired_role and not any(s["role"].lower() == desired_role.lower() for s in skill_matrix):
        skill_matrix.append({"role": desired_role.strip(), "source": "application", "confidence": "declared"})

    application = {
        "id": application_id,
        "status": "PENDING_REVIEW",
        "candidate_name": name,
        "email": (email or "").strip() or None,
        "phone": (phone or "").strip() or None,
        "resume_text": (resume_text or "")[:8000] or None,
        "desired_role": (desired_role or "").strip() or None,
        "skill_matrix": skill_matrix,
        "certifications": storage_files,
        "submitted_at": _now_iso(),
        "source": "www.basalt.co.il",
        "pending_flash": True,
    }

    matrix = await load_project_memory(org_id, project_id)
    apps = matrix.setdefault("basalt_applications", [])
    apps.append(application)
    matrix["basalt_applications"] = apps[-100:]
    bucket = matrix.setdefault("basalt_storage", {"bucket": f"ben-tenant-{org_id}", "objects": []})
    for f in storage_files:
        bucket["objects"].append({**f, "application_id": application_id, "uploaded_at": _now_iso()})
    bucket["objects"] = bucket["objects"][-500:]
    await save_project_memory(org_id, project_id, matrix)

    code = normalize_lang(lang)
    return {
        "application_id": application_id,
        "status": "PENDING_REVIEW",
        "project_id": str(project_id),
        "org_id": str(org_id),
        "skill_matrix": skill_matrix,
        "certification_count": len(storage_files),
        "storage_bucket": bucket["bucket"],
        "message": "Application received — pending compliance review." if code == "en" else "הבקשה התקבלה — ממתינה לבדיקת ציות.",
    }


async def fetch_verified_portfolio(org_id: uuid.UUID, *, lang: str | None = None) -> dict[str, Any]:
    """Live corporate portfolio from verified financial_ledger milestones."""
    code = normalize_lang(lang)
    projects_data = await list_projects(org_id)
    projects = projects_data.get("projects") or []
    milestones: list[dict[str, Any]] = []

    for proj in projects:
        pid = uuid.UUID(proj["id"])
        ledger_data = await list_ledger_entries(org_id, pid)
        for entry in ledger_data.get("entries") or []:
            if entry.get("entry_type") != "INCOME":
                continue
            status_val = (entry.get("status") or "").lower()
            if status_val not in ("recorded", "finalized", "pending"):
                continue
            desc = entry.get("description") or ""
            milestones.append(
                {
                    "project_id": str(pid),
                    "project_name": proj.get("name"),
                    "milestone": desc.replace("Customer invoice — milestone: ", "").strip() or desc[:120],
                    "amount_nis": entry.get("amount"),
                    "currency": entry.get("currency") or "ILS",
                    "status": entry.get("status"),
                    "verified": status_val in ("recorded", "finalized"),
                    "recorded_at": entry.get("created_at"),
                }
            )

    milestones.sort(key=lambda m: m.get("recorded_at") or "", reverse=True)
    return {
        "org_id": str(org_id),
        "lang": code,
        "portfolio": milestones[:50],
        "milestone_count": len(milestones),
        "content": build_corporate_content(code),
    }


async def list_pending_applications(org_id: uuid.UUID, project_id: uuid.UUID) -> list[dict[str, Any]]:
    matrix = await load_project_memory(org_id, project_id)
    apps = matrix.get("basalt_applications") or []
    return [a for a in apps if a.get("status") == "PENDING_REVIEW"]


async def mark_application_reviewed(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    application_id: str,
    *,
    new_status: str,
) -> dict[str, Any] | None:
    matrix = await load_project_memory(org_id, project_id)
    apps = matrix.get("basalt_applications") or []
    target = next((a for a in apps if a.get("id") == application_id), None)
    if target is None:
        return None
    target["status"] = new_status
    target["pending_flash"] = False
    target["reviewed_at"] = _now_iso()
    await save_project_memory(org_id, project_id, matrix)
    return target
