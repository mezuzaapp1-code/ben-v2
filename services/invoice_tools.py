"""Invoice capture and accounting export native tools (tenant-scoped)."""
from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select

from database.connection import get_db_session
from database.models import FinancialLedger, ProjectMember
from services.native_tools_service import _ledger_payload, _require_project, _set_org, create_ledger_entry
from services.ops.request_context import attach_request_id

_AMOUNT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})|\d+(?:\.\d{2})?)(?!\d)")
_VENDOR_SPLIT_RE = re.compile(r"[_\-\s]+")


def _parse_amount_from_text(text_val: str) -> float | None:
    if not text_val:
        return None
    best: float | None = None
    for match in _AMOUNT_RE.finditer(text_val.replace(",", "")):
        try:
            val = float(match.group(1).replace(" ", ""))
        except ValueError:
            continue
        if val > 0 and (best is None or val > best):
            best = val
    return best


def _parse_vendor_from_text(text_val: str) -> str | None:
    if not text_val:
        return None
    base = text_val.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    base = re.sub(r"\.(pdf|png|jpe?g|webp|heic)$", "", base, flags=re.I)
    parts = []
    for p in _VENDOR_SPLIT_RE.split(base):
        if not p or p.isdigit():
            continue
        if _parse_amount_from_text(p) is not None and re.fullmatch(r"[\d.,\s]+", p.replace(" ", "")):
            continue
        parts.append(p)
    if not parts:
        return None
    vendor = " ".join(parts[:3]).strip()
    return vendor[:256] if vendor else None


async def _match_vendor_member(
    session,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    vendor_name: str | None,
) -> dict[str, Any]:
    if not vendor_name:
        return {"matched": False, "member_id": None, "member_name": None, "match_status": "unmatched"}
    q = select(ProjectMember).where(
        ProjectMember.org_id == org_id,
        ProjectMember.project_id == project_id,
    )
    members = (await session.execute(q)).scalars().all()
    needle = vendor_name.strip().lower()
    for m in members:
        if m.name.strip().lower() == needle:
            return {
                "matched": True,
                "member_id": str(m.id),
                "member_name": m.name,
                "match_status": "exact",
            }
    for m in members:
        name_l = m.name.strip().lower()
        if needle in name_l or name_l in needle:
            return {
                "matched": True,
                "member_id": str(m.id),
                "member_name": m.name,
                "match_status": "partial",
            }
    return {"matched": False, "member_id": None, "member_name": None, "match_status": "unmatched"}


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
    """
    Extract vendor/amount/currency from capture metadata, match vendor to project members,
    and persist an EXPENSE row on financial_ledger.
    """
    source = (file_path or image_url or filename or "").strip()
    vendor = (vendor_hint or "").strip() or _parse_vendor_from_text(source)
    amount = amount_hint if amount_hint and amount_hint > 0 else _parse_amount_from_text(source)
    currency = (currency_hint or "USD").strip().upper()[:3] or "USD"

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

    payload = {
        "tool": "process_captured_invoice",
        "saved_to_ledger": True,
        "vendor": vendor,
        "amount": float(amount),
        "currency": currency,
        "vendor_match": vendor_match,
        "ledger_entry": ledger,
        "source": source or None,
    }
    return attach_request_id(payload)


async def export_ledger_to_accountant(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    format: str = "summary",
) -> dict[str, Any]:
    """Aggregate financial_ledger rows into a clean accountant-ready summary report."""
    fmt = (format or "summary").strip().lower()
    if fmt not in ("summary", "markdown"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "format must be summary or markdown")

    async with get_db_session() as session:
        await _set_org(session, org_id)
        project = await _require_project(session, org_id, project_id)
        q = (
            select(FinancialLedger)
            .where(FinancialLedger.org_id == org_id, FinancialLedger.project_id == project_id)
            .order_by(FinancialLedger.created_at.asc())
        )
        rows = (await session.execute(q)).scalars().all()

    entries = [_ledger_payload(r) for r in rows]
    income_total = Decimal("0")
    expense_total = Decimal("0")
    by_currency: dict[str, dict[str, Decimal]] = {}

    for e in entries:
        cur = e.get("currency") or "USD"
        bucket = by_currency.setdefault(cur, {"income": Decimal("0"), "expense": Decimal("0")})
        amt = Decimal(str(e.get("amount") or 0))
        if e.get("entry_type") == "INCOME":
            income_total += amt
            bucket["income"] += amt
        else:
            expense_total += amt
            bucket["expense"] += amt

    net = income_total - expense_total
    lines = [
        f"Project: {project.name}",
        f"Project ID: {project_id}",
        f"Entries: {len(entries)}",
        f"Income total: {income_total}",
        f"Expense total: {expense_total}",
        f"Net: {net}",
        "",
        "By currency:",
    ]
    for cur, totals in sorted(by_currency.items()):
        lines.append(f"  {cur}: income {totals['income']}, expense {totals['expense']}, net {totals['income'] - totals['expense']}")
    lines.append("")
    lines.append("Line items:")
    for e in entries:
        lines.append(
            f"  - [{e.get('entry_type')}] {e.get('amount')} {e.get('currency')} "
            f"({e.get('status')}) {e.get('description') or ''}".strip()
        )

    report_text = "\n".join(lines)
    payload = {
        "tool": "export_ledger_to_accountant",
        "format": fmt,
        "project_id": str(project_id),
        "project_name": project.name,
        "entry_count": len(entries),
        "totals": {
            "income": float(income_total),
            "expense": float(expense_total),
            "net": float(net),
        },
        "by_currency": {
            cur: {
                "income": float(v["income"]),
                "expense": float(v["expense"]),
                "net": float(v["income"] - v["expense"]),
            }
            for cur, v in by_currency.items()
        },
        "entries": entries,
        "report": report_text,
    }
    return attach_request_id(payload)
