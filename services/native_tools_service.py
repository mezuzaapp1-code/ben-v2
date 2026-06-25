"""Native project tools — transaction-safe CRUD for members, tasks, and ledger (tenant-scoped)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import (
    FINANCIAL_LEDGER_ENTRY_TYPES,
    FINANCIAL_LEDGER_STATUSES,
    FinancialLedger,
    PROJECT_MEMBER_TYPES,
    PROJECT_TASK_PRIORITIES,
    PROJECT_TASK_STATUSES,
    Project,
    ProjectMember,
    ProjectTask,
)
from services.ops.request_context import attach_request_id


async def _set_org(session: AsyncSession, org_id: uuid.UUID) -> None:
    await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": str(org_id)})


async def _require_project(session: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    row = await session.get(Project, project_id)
    if row is None or row.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return row


async def _require_member(
    session: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    member_id: uuid.UUID,
) -> ProjectMember:
    row = await session.get(ProjectMember, member_id)
    if row is None or row.org_id != org_id or row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project member not found")
    return row


def _parse_optional_date(raw: str | None) -> date | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid date; use ISO format YYYY-MM-DD") from e


def _member_payload(row: ProjectMember) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "org_id": str(row.org_id),
        "name": row.name,
        "member_type": row.member_type,
        "role": row.role,
        "hourly_rate": float(row.hourly_rate) if row.hourly_rate is not None else None,
        "email": row.email,
        "phone": row.phone,
        "contact_notes": row.contact_notes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _task_payload(row: ProjectTask) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "org_id": str(row.org_id),
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "priority": row.priority,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "assigned_to": str(row.assigned_to) if row.assigned_to else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _ledger_payload(row: FinancialLedger) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "org_id": str(row.org_id),
        "entry_type": row.entry_type,
        "amount": float(row.amount),
        "currency": row.currency,
        "description": row.description,
        "status": row.status,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# --- Members ---


async def add_project_member(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    name: str,
    member_type: str,
    role: str | None = None,
    hourly_rate: float | None = None,
    email: str | None = None,
    phone: str | None = None,
    contact_notes: str | None = None,
) -> dict[str, Any]:
    display_name = (name or "").strip()
    if not display_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name is required")
    mtype = (member_type or "").strip().upper()
    if mtype not in PROJECT_MEMBER_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"member_type must be one of: {', '.join(PROJECT_MEMBER_TYPES)}",
        )

    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        row = ProjectMember(
            project_id=project_id,
            org_id=org_id,
            name=display_name[:256],
            member_type=mtype,
            role=(role or "").strip() or None,
            hourly_rate=hourly_rate,
            email=(email or "").strip() or None,
            phone=(phone or "").strip() or None,
            contact_notes=(contact_notes or "").strip() or None,
        )
        session.add(row)
        await session.flush()
        await session.commit()
        payload = _member_payload(row)
    return attach_request_id(payload)


async def list_project_members(org_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        q = (
            select(ProjectMember)
            .where(ProjectMember.org_id == org_id, ProjectMember.project_id == project_id)
            .order_by(ProjectMember.name.asc())
        )
        rows = (await session.execute(q)).scalars().all()
        payload = {"members": [_member_payload(r) for r in rows]}
    return attach_request_id(payload)


async def get_project_member(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    member_id: uuid.UUID,
) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await _require_member(session, org_id, project_id, member_id)
        payload = _member_payload(row)
    return attach_request_id(payload)


async def update_project_member(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    member_id: uuid.UUID,
    *,
    name: str | None = None,
    member_type: str | None = None,
    role: str | None = None,
    hourly_rate: float | None = None,
    email: str | None = None,
    phone: str | None = None,
    contact_notes: str | None = None,
) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        row = await _require_member(session, org_id, project_id, member_id)
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name cannot be empty")
            row.name = cleaned[:256]
        if member_type is not None:
            mtype = member_type.strip().upper()
            if mtype not in PROJECT_MEMBER_TYPES:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"member_type must be one of: {', '.join(PROJECT_MEMBER_TYPES)}",
                )
            row.member_type = mtype
        if role is not None:
            row.role = role.strip() or None
        if hourly_rate is not None:
            row.hourly_rate = hourly_rate
        if email is not None:
            row.email = email.strip() or None
        if phone is not None:
            row.phone = phone.strip() or None
        if contact_notes is not None:
            row.contact_notes = contact_notes.strip() or None
        await session.flush()
        await session.commit()
        payload = _member_payload(row)
    return attach_request_id(payload)


# --- Tasks ---


async def create_project_task(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    title: str,
    description: str | None = None,
    status: str = "todo",
    priority: str = "medium",
    due_date: str | None = None,
    assigned_to: uuid.UUID | None = None,
) -> dict[str, Any]:
    task_title = (title or "").strip()
    if not task_title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "title is required")
    status_norm = (status or "todo").strip().lower()
    if status_norm not in PROJECT_TASK_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"status must be one of: {', '.join(PROJECT_TASK_STATUSES)}",
        )
    priority_norm = (priority or "medium").strip().lower()
    if priority_norm not in PROJECT_TASK_PRIORITIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"priority must be one of: {', '.join(PROJECT_TASK_PRIORITIES)}",
        )
    due = _parse_optional_date(due_date)

    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        if assigned_to is not None:
            await _require_member(session, org_id, project_id, assigned_to)
        row = ProjectTask(
            project_id=project_id,
            org_id=org_id,
            title=task_title[:512],
            description=(description or "").strip() or None,
            status=status_norm,
            priority=priority_norm,
            due_date=due,
            assigned_to=assigned_to,
        )
        session.add(row)
        await session.flush()
        await session.commit()
        payload = _task_payload(row)
    return attach_request_id(payload)


async def list_project_tasks(org_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        q = (
            select(ProjectTask)
            .where(ProjectTask.org_id == org_id, ProjectTask.project_id == project_id)
            .order_by(ProjectTask.due_date.asc().nulls_last(), ProjectTask.created_at.desc())
        )
        rows = (await session.execute(q)).scalars().all()
        payload = {"tasks": [_task_payload(r) for r in rows]}
    return attach_request_id(payload)


async def get_project_task(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        row = await session.get(ProjectTask, task_id)
        if row is None or row.org_id != org_id or row.project_id != project_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project task not found")
        payload = _task_payload(row)
    return attach_request_id(payload)


async def update_project_task(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    *,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    due_date: str | None = None,
    assigned_to: uuid.UUID | None = None,
    clear_assigned_to: bool = False,
) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        row = await session.get(ProjectTask, task_id)
        if row is None or row.org_id != org_id or row.project_id != project_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project task not found")

        if title is not None:
            cleaned = title.strip()
            if not cleaned:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "title cannot be empty")
            row.title = cleaned[:512]
        if description is not None:
            row.description = description.strip() or None
        if status is not None:
            status_norm = status.strip().lower()
            if status_norm not in PROJECT_TASK_STATUSES:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"status must be one of: {', '.join(PROJECT_TASK_STATUSES)}",
                )
            row.status = status_norm
        if priority is not None:
            priority_norm = priority.strip().lower()
            if priority_norm not in PROJECT_TASK_PRIORITIES:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"priority must be one of: {', '.join(PROJECT_TASK_PRIORITIES)}",
                )
            row.priority = priority_norm
        if due_date is not None:
            row.due_date = _parse_optional_date(due_date)
        if clear_assigned_to:
            row.assigned_to = None
        elif assigned_to is not None:
            await _require_member(session, org_id, project_id, assigned_to)
            row.assigned_to = assigned_to

        await session.flush()
        await session.commit()
        payload = _task_payload(row)
    return attach_request_id(payload)


# --- Ledger ---


async def create_ledger_entry(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    entry_type: str,
    amount: float,
    currency: str = "USD",
    description: str | None = None,
    status: str = "pending",
    due_date: str | None = None,
) -> dict[str, Any]:
    etype = (entry_type or "").strip().upper()
    if etype not in FINANCIAL_LEDGER_ENTRY_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"entry_type must be one of: {', '.join(FINANCIAL_LEDGER_ENTRY_TYPES)}",
        )
    try:
        amount_val = Decimal(str(amount))
    except Exception as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid amount") from e
    if amount_val <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "amount must be positive")

    currency_norm = (currency or "USD").strip().upper()[:3]
    if len(currency_norm) != 3:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "currency must be a 3-letter ISO code")

    status_norm = (status or "pending").strip().lower()
    if status_norm not in FINANCIAL_LEDGER_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"status must be one of: {', '.join(FINANCIAL_LEDGER_STATUSES)}",
        )
    due = _parse_optional_date(due_date)

    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        row = FinancialLedger(
            project_id=project_id,
            org_id=org_id,
            entry_type=etype,
            amount=amount_val,
            currency=currency_norm,
            description=(description or "").strip() or None,
            status=status_norm,
            due_date=due,
        )
        session.add(row)
        await session.flush()
        await session.commit()
        payload = _ledger_payload(row)
    return attach_request_id(payload)


async def list_ledger_entries(org_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        q = (
            select(FinancialLedger)
            .where(FinancialLedger.org_id == org_id, FinancialLedger.project_id == project_id)
            .order_by(FinancialLedger.due_date.asc().nulls_last(), FinancialLedger.created_at.desc())
        )
        rows = (await session.execute(q)).scalars().all()
        payload = {"entries": [_ledger_payload(r) for r in rows]}
    return attach_request_id(payload)


async def get_ledger_entry(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        row = await session.get(FinancialLedger, entry_id)
        if row is None or row.org_id != org_id or row.project_id != project_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ledger entry not found")
        payload = _ledger_payload(row)
    return attach_request_id(payload)


async def update_ledger_entry(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    entry_id: uuid.UUID,
    *,
    entry_type: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    description: str | None = None,
    status: str | None = None,
    due_date: str | None = None,
) -> dict[str, Any]:
    async with get_db_session() as session:
        await _set_org(session, org_id)
        await _require_project(session, org_id, project_id)
        row = await session.get(FinancialLedger, entry_id)
        if row is None or row.org_id != org_id or row.project_id != project_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ledger entry not found")

        if entry_type is not None:
            etype = entry_type.strip().upper()
            if etype not in FINANCIAL_LEDGER_ENTRY_TYPES:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"entry_type must be one of: {', '.join(FINANCIAL_LEDGER_ENTRY_TYPES)}",
                )
            row.entry_type = etype
        if amount is not None:
            try:
                amount_val = Decimal(str(amount))
            except Exception as e:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid amount") from e
            if amount_val <= 0:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "amount must be positive")
            row.amount = amount_val
        if currency is not None:
            currency_norm = currency.strip().upper()[:3]
            if len(currency_norm) != 3:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "currency must be a 3-letter ISO code")
            row.currency = currency_norm
        if description is not None:
            row.description = description.strip() or None
        if status is not None:
            status_norm = status.strip().lower()
            if status_norm not in FINANCIAL_LEDGER_STATUSES:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"status must be one of: {', '.join(FINANCIAL_LEDGER_STATUSES)}",
                )
            row.status = status_norm
        if due_date is not None:
            row.due_date = _parse_optional_date(due_date)

        await session.flush()
        await session.commit()
        payload = _ledger_payload(row)
    return attach_request_id(payload)
