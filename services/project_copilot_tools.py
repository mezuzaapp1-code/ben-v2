"""Project copilot native tools — quotation flow, ledger, forecast, lifecycle."""
from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select

from database.connection import get_db_session
from database.models import FinancialLedger
from services.invoice_tools import _match_vendor_member, _parse_amount_from_text, _parse_vendor_from_text
from services.native_tools_service import _ledger_payload, _require_project, _set_org, create_ledger_entry
from services.ops.request_context import attach_request_id
from services.project_memory_service import (
    QUOTATION_STEPS,
    compute_location_logistics,
    compute_subsistence_overhead,
    lifecycle_analytics,
    load_project_memory,
    refresh_subsistence_in_matrix,
    save_project_memory,
    touch_lifecycle_phase,
)

_STEP_ORDER = [key for key, _ in QUOTATION_STEPS]
_STEP_LABELS = {key: label for key, label in QUOTATION_STEPS}

_LOCATION_RE = re.compile(
    r"\b(?:to|at|in|near)\s+([A-Za-z\u0590-\u05FF][A-Za-z\u0590-\u05FF\s\-]{1,40})",
    re.I,
)


def attach_mutated_state(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    card_map = {
        "initiate_quotation_flow": "quotation_deliberation",
        "process_captured_invoice": "receipt_capture",
        "process_credit_memo": "credit_memo",
        "issue_customer_invoice": "customer_invoice",
        "get_cash_flow_forecast": "cash_flow_forecast",
        "get_lifecycle_overview": "lifecycle_overview",
        "fetch_site_intelligence": "government_intelligence",
        "initiate_tactical_quotation": "government_intelligence",
        "onboard_project_member": "compliance_insurance",
        "log_daily_operations": "next_day_briefing",
        "process_worker_response": "daily_attendance_delay",
        "analyze_supplier_tender": "cost_engineering_bid_tabulation",
        "define_tactical_job_requirements": "upskilling_strategy",
        "simulate_training_day_roi": "onsite_proctor_session",
        "review_basalt_application": "basalt_web_application",
    }
    card_type = card_map.get(tool_name)
    if not card_type:
        return result
    return {
        **result,
        "mutated_state": {"card_type": card_type, "payload": result},
    }


async def initiate_quotation_flow(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    action: str = "start",
    step_key: str | None = None,
    step_data: dict[str, Any] | None = None,
    target_location: str | None = None,
) -> dict[str, Any]:
    """Guided quotation state machine across location → materials → risk → labor."""
    act = (action or "start").strip().lower()
    matrix = await load_project_memory(org_id, project_id)
    flow = matrix.setdefault("quotation_flow", {"active": False, "current_step": None, "steps": {}})
    steps = flow.setdefault("steps", {k: {"label": v, "completed": False, "data": {}} for k, v in QUOTATION_STEPS})

    if act == "start":
        flow["active"] = True
        flow["current_step"] = _STEP_ORDER[0]
        await touch_lifecycle_phase(org_id, project_id, "quote_initialization")
        matrix = await load_project_memory(org_id, project_id)
        flow = matrix["quotation_flow"]

    elif act == "advance":
        key = (step_key or flow.get("current_step") or _STEP_ORDER[0]).strip()
        if key not in _STEP_ORDER:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown quotation step: {key}")
        data = step_data or {}
        if key == "location":
            loc = (target_location or data.get("target_location") or data.get("location") or "").strip()
            if loc:
                logistics = compute_location_logistics(loc)
                matrix.setdefault("location_logistics", {"targets": {}})["targets"][loc] = logistics
                data = {**data, "target_location": loc, "logistics": logistics}
        if key == "labor_execution":
            days = int(data.get("task_days") or data.get("duration_days") or 1)
            matrix = await refresh_subsistence_in_matrix(org_id, project_id, task_days=days)
            data = {**data, "subsistence": matrix.get("subsistence")}
            est = float(data.get("estimated_total_nis") or data.get("total_cost_nis") or 0)
            if est > 0:
                matrix.setdefault("estimates", {})["total_cost_nis"] = est
        steps[key] = {**steps.get(key, {}), "label": _STEP_LABELS[key], "completed": True, "data": data}
        flow["steps"] = steps
        idx = _STEP_ORDER.index(key)
        if idx < len(_STEP_ORDER) - 1:
            flow["current_step"] = _STEP_ORDER[idx + 1]
        else:
            flow["current_step"] = None
            flow["active"] = False
            await touch_lifecycle_phase(org_id, project_id, "activation")
            matrix = await load_project_memory(org_id, project_id)
            flow = matrix["quotation_flow"]
            steps = flow["steps"]
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "action must be start or advance")

    await save_project_memory(org_id, project_id, matrix)
    checklist = [
        {
            "key": k,
            "label": _STEP_LABELS[k],
            "completed": bool(steps.get(k, {}).get("completed")),
            "current": flow.get("current_step") == k,
            "data": steps.get(k, {}).get("data") or {},
        }
        for k in _STEP_ORDER
    ]
    payload = {
        "tool": "initiate_quotation_flow",
        "action": act,
        "active": flow.get("active", False),
        "current_step": flow.get("current_step"),
        "checklist": checklist,
        "prompt": _quotation_prompt(flow.get("current_step")),
        "memory_snapshot": {
            "location_logistics": matrix.get("location_logistics"),
            "subsistence": matrix.get("subsistence"),
            "estimates": matrix.get("estimates"),
        },
    }
    return attach_request_id(attach_mutated_state("initiate_quotation_flow", payload))


def _quotation_prompt(step: str | None) -> str | None:
    prompts = {
        "location": "Where is the project site? (e.g., Shoham) — travel from Or Akiva will be calculated automatically.",
        "materials_suppliers": "List primary materials and preferred suppliers for this quote.",
        "risk_mitigation": "Identify top risks and mitigation steps (weather, permits, supply delays).",
        "labor_execution": "Confirm crew size, duration (days), and execution plan.",
    }
    return prompts.get(step or "")


async def process_captured_invoice(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    file_path: str | None = None,
    image_url: str | None = None,
    filename: str | None = None,
    vendor_hint: str | None = None,
    amount_hint: float | None = None,
    currency_hint: str | None = None,
) -> dict[str, Any]:
    source = (file_path or image_url or filename or "").strip()
    vendor = (vendor_hint or "").strip() or _parse_vendor_from_text(source)
    amount = amount_hint if amount_hint and amount_hint > 0 else _parse_amount_from_text(source)
    currency = (currency_hint or "ILS").strip().upper()[:3] or "ILS"

    if amount is None or amount <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Could not extract a positive amount from the invoice capture",
        )

    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        vendor_match = await _match_vendor_member(session, org_id, project_id, vendor)

    description_parts = ["Invoice capture"]
    if source:
        description_parts.append(source)
    if vendor_match["matched"]:
        description_parts.append(f"vendor match: {vendor_match['member_name']} ({vendor_match['match_status']})")

    ledger = await create_ledger_entry(
        org_id,
        project_id,
        entry_type="EXPENSE",
        amount=float(amount),
        currency=currency,
        description=" — ".join(description_parts)[:4000],
        status="recorded",
    )

    matrix = await load_project_memory(org_id, project_id)
    actual = float(matrix.get("estimates", {}).get("actual_cost_nis") or 0) + float(amount)
    matrix.setdefault("estimates", {})["actual_cost_nis"] = round(actual, 2)
    await save_project_memory(org_id, project_id, matrix)
    await touch_lifecycle_phase(org_id, project_id, "execution")

    payload = {
        "tool": "process_captured_invoice",
        "document_type": "invoice",
        "saved_to_ledger": True,
        "vendor": vendor,
        "amount": float(amount),
        "currency": currency,
        "vendor_match": vendor_match,
        "ledger_entry": ledger,
        "source": source or None,
    }
    return attach_request_id(attach_mutated_state("process_captured_invoice", payload))


async def process_credit_memo(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    file_path: str | None = None,
    image_url: str | None = None,
    filename: str | None = None,
    vendor_hint: str | None = None,
    amount_hint: float | None = None,
    currency_hint: str | None = None,
) -> dict[str, Any]:
    """Credit memo — logged as INCOME (vendor refund / negative expense adjustment)."""
    source = (file_path or image_url or filename or "").strip()
    vendor = (vendor_hint or "").strip() or _parse_vendor_from_text(source)
    amount = amount_hint if amount_hint and amount_hint > 0 else _parse_amount_from_text(source)
    currency = (currency_hint or "ILS").strip().upper()[:3] or "ILS"

    if amount is None or amount <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Could not extract a positive credit amount from the memo capture",
        )

    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        vendor_match = await _match_vendor_member(session, org_id, project_id, vendor)

    ledger = await create_ledger_entry(
        org_id,
        project_id,
        entry_type="INCOME",
        amount=float(amount),
        currency=currency,
        description=f"Credit memo — {vendor or 'vendor'} — {source or 'capture'}"[:4000],
        status="recorded",
    )

    matrix = await load_project_memory(org_id, project_id)
    actual = float(matrix.get("estimates", {}).get("actual_cost_nis") or 0) - float(amount)
    matrix.setdefault("estimates", {})["actual_cost_nis"] = round(max(0, actual), 2)
    await save_project_memory(org_id, project_id, matrix)

    payload = {
        "tool": "process_credit_memo",
        "document_type": "credit_memo",
        "saved_to_ledger": True,
        "vendor": vendor,
        "amount": float(amount),
        "currency": currency,
        "vendor_match": vendor_match,
        "ledger_entry": ledger,
        "source": source or None,
        "adjustment": "negative_expense",
    }
    return attach_request_id(attach_mutated_state("process_credit_memo", payload))


async def issue_customer_invoice(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    milestone: str,
    amount: float,
    currency: str = "ILS",
    due_date: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    if not milestone or not milestone.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "milestone is required")
    if amount <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "amount must be positive")

    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)

    desc = description or f"Customer invoice — milestone: {milestone.strip()}"
    ledger = await create_ledger_entry(
        org_id,
        project_id,
        entry_type="INCOME",
        amount=float(amount),
        currency=(currency or "ILS").upper()[:3],
        description=desc[:4000],
        status="pending",
        due_date=due_date,
    )
    await touch_lifecycle_phase(org_id, project_id, "execution")

    payload = {
        "tool": "issue_customer_invoice",
        "saved_to_ledger": True,
        "milestone": milestone.strip(),
        "amount": float(amount),
        "currency": (currency or "ILS").upper()[:3],
        "ledger_entry": ledger,
        "status": "pending",
    }
    return attach_request_id(attach_mutated_state("issue_customer_invoice", payload))


async def get_cash_flow_forecast(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    horizon_weeks: int = 8,
    safety_threshold_nis: float = 5000,
) -> dict[str, Any]:
    weeks = max(4, min(24, int(horizon_weeks or 8)))
    threshold = float(safety_threshold_nis or 5000)

    async with get_db_session() as session:
        await _set_org(session, org_id)
        project = await _require_project(session, org_id, project_id)
        q = select(FinancialLedger).where(
            FinancialLedger.org_id == org_id,
            FinancialLedger.project_id == project_id,
        )
        rows = (await session.execute(q)).scalars().all()

    entries = [_ledger_payload(r) for r in rows]
    finalized_income = Decimal("0")
    finalized_expense = Decimal("0")
    pending_income = Decimal("0")
    pending_expense = Decimal("0")

    for e in entries:
        amt = Decimal(str(e.get("amount") or 0))
        status_val = (e.get("status") or "").lower()
        is_pending = status_val in ("pending",)
        if e.get("entry_type") == "INCOME":
            if is_pending:
                pending_income += amt
            else:
                finalized_income += amt
        else:
            if is_pending:
                pending_expense += amt
            else:
                finalized_expense += amt

    net_finalized = finalized_income - finalized_expense
    net_with_pending = net_finalized + pending_income - pending_expense
    weekly_inflow = (pending_income / max(1, weeks)).quantize(Decimal("0.01"))
    weekly_outflow = (pending_expense / max(1, weeks)).quantize(Decimal("0.01"))

    runway: list[dict[str, Any]] = []
    balance = net_finalized
    for w in range(1, weeks + 1):
        balance += weekly_inflow - weekly_outflow
        runway.append({"week": w, "balance": float(balance)})

    safety_trigger = float(net_with_pending) < threshold
    payload = {
        "tool": "get_cash_flow_forecast",
        "project_id": str(project_id),
        "project_name": project.name,
        "totals": {
            "finalized_income": float(finalized_income),
            "finalized_expense": float(finalized_expense),
            "pending_income": float(pending_income),
            "pending_expense": float(pending_expense),
            "net_finalized": float(net_finalized),
            "net_with_pending": float(net_with_pending),
        },
        "runway_weeks": runway,
        "safety_threshold_nis": threshold,
        "safety_trigger": safety_trigger,
        "safety_message": (
            "Cash balance below safety threshold — review pending expenses."
            if safety_trigger
            else "Cash position within safety threshold."
        ),
    }
    return attach_request_id(attach_mutated_state("get_cash_flow_forecast", payload))


async def get_lifecycle_overview(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        project = await _require_project(session, org_id, project_id)

    matrix = await load_project_memory(org_id, project_id)
    analytics = lifecycle_analytics(matrix)
    payload = {
        "tool": "get_lifecycle_overview",
        "project_id": str(project_id),
        "project_name": project.name,
        "project_status": project.status,
        **analytics,
        "subsistence": matrix.get("subsistence"),
        "location_logistics": matrix.get("location_logistics"),
    }
    return attach_request_id(attach_mutated_state("get_lifecycle_overview", payload))


async def infer_location_from_message(message: str) -> str | None:
    m = _LOCATION_RE.search(message or "")
    if not m:
        return None
    return m.group(1).strip()


async def apply_ambient_memory_from_message(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    message: str,
) -> dict[str, Any] | None:
    """Infuse logistics when a project location is mentioned in chat."""
    loc = await infer_location_from_message(message)
    if not loc:
        return None
    matrix = await load_project_memory(org_id, project_id)
    logistics = compute_location_logistics(loc)
    matrix.setdefault("location_logistics", {"targets": {}})["targets"][loc] = logistics
    await save_project_memory(org_id, project_id, matrix)
    return logistics
