"""Detect copilot tool intents from chat messages and execute with mutated_state."""
from __future__ import annotations

import re
import uuid
from typing import Any

from services.project_copilot_tools import (
    apply_ambient_memory_from_message,
    get_cash_flow_forecast,
    get_lifecycle_overview,
    initiate_quotation_flow,
    issue_customer_invoice,
    process_credit_memo,
)
from services.tactical_copilot_tools import (
    fetch_site_intelligence,
    initiate_tactical_quotation,
    log_daily_operations,
    onboard_project_member,
)

_QUOTE_RE = re.compile(r"@quote|start\s+quotation|quotation\s+flow|begin\s+quote", re.I)
_FORECAST_RE = re.compile(r"@forecast|cash\s*flow|runway|balance\s+forecast", re.I)
_LIFECYCLE_RE = re.compile(r"@lifecycle|project\s+timeline|margin\s+variance", re.I)
_CREDIT_RE = re.compile(r"credit\s+memo|@credit", re.I)
_INVOICE_ISSUE_RE = re.compile(r"issue\s+(?:customer\s+)?invoice|bill\s+milestone|@bill", re.I)
_MILESTONE_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:nis|ils|₪)?", re.I)
_INTEL_RE = re.compile(r"@intel|site\s+intelligence|safety\s+registry|ministry\s+of\s+labor", re.I)
_TACTICAL_RE = re.compile(r"@tactical|tactical\s+quotation|hazard\s+map", re.I)
_ONBOARD_RE = re.compile(r"@onboard|onboard\s+worker|add\s+worker", re.I)
_DAILY_OPS_RE = re.compile(r"@daily|log\s+operations|next[\s-]?day\s+brief", re.I)
_ATTENDANCE_RE = re.compile(
    r"@attendance|time\s*card|worker\s+(?:hours?|response)|clocked\s+in|partial\s+shift",
    re.I,
)
_WORKER_NAME_RE = re.compile(
    r"(?:worker|employee)\s+([A-Za-z\u0590-\u05FF][A-Za-z\u0590-\u05FF\s\-]{1,30})",
    re.I,
)
_TENDER_RE = re.compile(
    r"@tender|supplier\s+(?:bid|quote|tender)|analyze\s+(?:bid|tender)|cost\s+engineering",
    re.I,
)
_UPSKILL_RE = re.compile(
    r"@upskill|job\s+requirements?|skill\s+blueprint|tactical\s+requirements?",
    re.I,
)
_TRAINING_RE = re.compile(
    r"@training|training\s+day|proctor|certification\s+gap|upskilling",
    re.I,
)
_BASALT_RE = re.compile(
    r"@basalt|basalt\.co|candidate\s+application|resume\s+submitted|web\s+application",
    re.I,
)
_SITE_RE = re.compile(
    r"\b(?:to|at|in|near)\s+([A-Za-z\u0590-\u05FF][A-Za-z\u0590-\u05FF\s\-]{1,40})",
    re.I,
)


async def run_copilot_preamble(
    message: str,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    """
    Execute project copilot tools before LLM stream; returns NDJSON-ready mutated_state events.
    """
    if project_id is None:
        return []

    text = (message or "").strip()
    if not text:
        return []

    events: list[dict[str, Any]] = []

    logistics = await apply_ambient_memory_from_message(org_id, project_id, text)
    if logistics:
        events.append(
            {
                "type": "mutated_state",
                "card_type": "lifecycle_overview",
                "payload": {
                    "tool": "ambient_location_logistics",
                    "location_logistics": {"targets": {logistics["target"]: logistics}},
                    "message": f"Travel from {logistics['base']} to {logistics['target']}: "
                    f"{logistics['round_trip_km']} km round-trip, ~{logistics['round_trip_min']} min, "
                    f"fuel ≈ ₪{logistics['fuel_nis']}.",
                },
            }
        )

    if _QUOTE_RE.search(text):
        result = await initiate_quotation_flow(org_id, project_id, action="start")
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _FORECAST_RE.search(text):
        result = await get_cash_flow_forecast(org_id, project_id)
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _LIFECYCLE_RE.search(text):
        result = await get_lifecycle_overview(org_id, project_id)
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _CREDIT_RE.search(text):
        from services.invoice_tools import _parse_amount_from_text

        amount = _parse_amount_from_text(text)
        if amount:
            result = await process_credit_memo(
                org_id, project_id, filename=text[:256], amount_hint=amount
            )
            ms = result.get("mutated_state") or {}
            events.append({"type": "mutated_state", **ms})

    if _INVOICE_ISSUE_RE.search(text):
        milestone = "Milestone billing"
        m = re.search(r"milestone[:\s]+([^,\n]+)", text, re.I)
        if m:
            milestone = m.group(1).strip()[:256]
        amt_m = _MILESTONE_AMOUNT_RE.search(text)
        amount = float(amt_m.group(1)) if amt_m else 1000.0
        result = await issue_customer_invoice(
            org_id, project_id, milestone=milestone, amount=amount, currency="ILS"
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    site_m = _SITE_RE.search(text)
    site_address = site_m.group(1).strip() if site_m else None

    if _INTEL_RE.search(text) or (site_address and "intel" in text.lower()):
        result = await fetch_site_intelligence(
            org_id, project_id, site_address=site_address, contractor_name=None
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _TACTICAL_RE.search(text):
        result = await initiate_tactical_quotation(
            org_id,
            project_id,
            site_address=site_address,
            base_quote_nis=50000,
            crew_size=3,
            duration_days=5,
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _ONBOARD_RE.search(text):
        name_m = re.search(r"(?:worker|member|onboard)\s+([A-Za-z\u0590-\u05FF][A-Za-z\u0590-\u05FF\s\-]{1,40})", text, re.I)
        worker_name = name_m.group(1).strip() if name_m else "Field Worker"
        result = await onboard_project_member(
            org_id,
            project_id,
            name=worker_name,
            member_type="EMPLOYEE",
            insurance_policy_id="POL-2026-ACTIVE-4412",
            contract_valid_until="2026-12-31",
            safety_profile_score=82,
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _DAILY_OPS_RE.search(text):
        materials_m = re.search(r"materials?[:\s]+([^.\n]+)", text, re.I)
        materials = []
        if materials_m:
            materials = [p.strip() for p in materials_m.group(1).split(",") if p.strip()]
        result = await log_daily_operations(
            org_id,
            project_id,
            clocked_hours=9,
            friction_events=["delayed_concrete_delivery"] if "friction" in text.lower() else [],
            next_day_materials=materials or ["rebar_12mm", "formwork_panels"],
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _ATTENDANCE_RE.search(text):
        from services.model_gateway import process_worker_response

        name_m = _WORKER_NAME_RE.search(text)
        worker_name = name_m.group(1).strip() if name_m else "Field Worker"
        result = await process_worker_response(
            org_id,
            project_id,
            worker_name=worker_name,
            response_text=text,
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _TENDER_RE.search(text):
        from services.model_gateway import analyze_supplier_tender

        result = await analyze_supplier_tender(
            org_id,
            project_id,
            action="analyze",
            bid_text=text,
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _UPSKILL_RE.search(text):
        from services.model_gateway import define_tactical_job_requirements

        result = await define_tactical_job_requirements(
            org_id,
            project_id,
            engineering_scope=text,
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _TRAINING_RE.search(text):
        from services.model_gateway import simulate_training_day_roi

        result = await simulate_training_day_roi(
            org_id,
            project_id,
            action="simulate",
            engineering_scope=text,
        )
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    if _BASALT_RE.search(text):
        from services.model_gateway import review_basalt_application

        result = await review_basalt_application(org_id, project_id, action="inbox")
        ms = result.get("mutated_state") or {}
        events.append({"type": "mutated_state", **ms})

    return events
