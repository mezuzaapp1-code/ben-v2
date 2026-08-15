"""Project management API — Clerk JWT or closed-beta passcode (Task 010)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from auth.project_privileges import assert_can_create_project
from auth.beta_gate import build_project_tenant_context_from_request
from auth.tenant_binding import validate_body_tenant_matches_context
from services.project_memory_service import initialize_project_setup, load_project_memory, save_project_memory
from services.project_schema_generator import provision_conversational_workspace_schema
from services.model_gateway import NATIVE_TOOL_DEFINITIONS, execute_native_tool
from services.invoice_tools import export_ledger_to_accountant
from services.project_copilot_tools import process_captured_invoice, process_credit_memo
from services.native_tools_service import (
    add_project_member,
    create_ledger_entry,
    create_project_task,
    get_ledger_entry,
    get_project_member,
    get_project_task,
    list_ledger_entries,
    list_project_members,
    list_project_tasks,
    update_ledger_entry,
    update_project_member,
    update_project_task,
)
from services.ops.timing import measure
from services.project_tools import create_project_directory, slugify_project_name
from services.project_service import create_project, get_project, list_projects

router = APIRouter(prefix="/api/projects", tags=["projects"])


class TenantScopedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(
        None,
        description="Optional; if present must match authenticated tenant scope exactly",
    )


class ProjectCreateBody(TenantScopedBody):
    name: str = Field(..., min_length=1, max_length=512)
    description: str | None = Field(None, max_length=8000)
    status: str = Field("active", max_length=32)
    location_base: str | None = Field(None, max_length=256)
    key_contacts: str | None = Field(None, max_length=8000)
    initial_tactical_tasks: str | None = Field(None, max_length=8000)


class SchemaColumnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    data_type: str = Field("text", min_length=1, max_length=32)
    primary_key: bool = False
    nullable: bool = True
    unique: bool = False


class SchemaTableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    columns: list[SchemaColumnBody] = Field(..., min_length=1, max_length=32)


class ConversationalInitBody(TenantScopedBody):
    name: str = Field(..., min_length=1, max_length=512)
    software_description: str = Field(..., min_length=1, max_length=16000)
    description: str | None = Field(None, max_length=8000)
    status: str = Field("active", max_length=32)
    location_base: str | None = Field(None, max_length=256)
    key_contacts: str | None = Field(None, max_length=8000)
    initial_tactical_tasks: str | None = Field(None, max_length=8000)
    schema_tables: list[SchemaTableBody] | None = Field(
        None,
        description="Optional explicit relational blueprint; otherwise inferred from software_description",
    )


async def _seed_initial_tactical_tasks(
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    raw: str | None,
) -> None:
    if not raw or not raw.strip():
        return
    for line in raw.splitlines():
        title = line.strip()
        if not title:
            continue
        await create_project_task(
            org_id,
            project_id,
            title=title[:512],
            status="todo",
            priority="high",
        )


class MemberCreateBody(TenantScopedBody):
    name: str = Field(..., min_length=1, max_length=256)
    member_type: str = Field(..., description="EMPLOYEE or VENDOR")
    role: str | None = Field(None, max_length=128)
    hourly_rate: float | None = Field(None, ge=0)
    email: str | None = Field(None, max_length=320)
    phone: str | None = Field(None, max_length=64)
    contact_notes: str | None = Field(None, max_length=4000)


class MemberUpdateBody(TenantScopedBody):
    name: str | None = Field(None, min_length=1, max_length=256)
    member_type: str | None = Field(None, description="EMPLOYEE or VENDOR")
    role: str | None = Field(None, max_length=128)
    hourly_rate: float | None = Field(None, ge=0)
    email: str | None = Field(None, max_length=320)
    phone: str | None = Field(None, max_length=64)
    contact_notes: str | None = Field(None, max_length=4000)


class TaskCreateBody(TenantScopedBody):
    title: str = Field(..., min_length=1, max_length=512)
    description: str | None = Field(None, max_length=8000)
    status: str = Field("todo", max_length=32)
    priority: str = Field("medium", max_length=16)
    due_date: str | None = Field(None, description="ISO date YYYY-MM-DD")
    assigned_to: str | None = Field(None, description="Project member UUID")


class TaskUpdateBody(TenantScopedBody):
    title: str | None = Field(None, min_length=1, max_length=512)
    description: str | None = Field(None, max_length=8000)
    status: str | None = Field(None, max_length=32)
    priority: str | None = Field(None, max_length=16)
    due_date: str | None = Field(None, description="ISO date YYYY-MM-DD")
    assigned_to: str | None = Field(None, description="Project member UUID")
    clear_assigned_to: bool = False


class LedgerCreateBody(TenantScopedBody):
    entry_type: str = Field(..., description="INCOME or EXPENSE")
    amount: float = Field(..., gt=0)
    currency: str = Field("USD", min_length=3, max_length=3)
    description: str | None = Field(None, max_length=4000)
    status: str = Field("pending", max_length=32)
    due_date: str | None = Field(None, description="ISO date YYYY-MM-DD")


class LedgerUpdateBody(TenantScopedBody):
    entry_type: str | None = Field(None, description="INCOME or EXPENSE")
    amount: float | None = Field(None, gt=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    description: str | None = Field(None, max_length=4000)
    status: str | None = Field(None, max_length=32)
    due_date: str | None = Field(None, description="ISO date YYYY-MM-DD")


class InvoiceCaptureBody(TenantScopedBody):
    file_path: str | None = Field(None, max_length=2048)
    image_url: str | None = Field(None, max_length=2048)
    filename: str | None = Field(None, max_length=512)
    vendor_hint: str | None = Field(None, max_length=256)
    amount_hint: float | None = Field(None, gt=0)
    currency_hint: str | None = Field(None, min_length=3, max_length=3)


class LedgerExportBody(TenantScopedBody):
    format: str = Field("summary", description="summary or markdown")


class NativeToolExecuteBody(TenantScopedBody):
    tool_name: str = Field(..., min_length=1, max_length=128)
    arguments: dict = Field(default_factory=dict)


def _parse_uuid(raw: str, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw).strip())
    except ValueError as e:
        raise HTTPException(422, f"Invalid {field}") from e


def _org_from_ctx(ctx) -> uuid.UUID:
    return uuid.UUID(ctx.tenant_id)


# --- Projects ---


@router.get("")
async def api_list_projects(request: Request):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects"
    )
    async with measure(subsystem="projects", operation="GET /api/projects"):
        return await list_projects(_org_from_ctx(ctx))


@router.post("")
async def api_create_project(request: Request, body: ProjectCreateBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="POST /api/projects"
    )
    validate_body_tenant_matches_context(body, ctx)
    assert_can_create_project(ctx)
    org_id = _org_from_ctx(ctx)
    async with measure(subsystem="projects", operation="POST /api/projects"):
        created = await create_project(
            org_id,
            name=body.name,
            description=body.description,
            status=body.status,
        )
        project_id = uuid.UUID(created["id"])
        await initialize_project_setup(
            org_id,
            project_id,
            location_base=body.location_base,
            key_contacts=body.key_contacts,
            initial_tactical_tasks=body.initial_tactical_tasks,
        )
        await _seed_initial_tactical_tasks(org_id, project_id, body.initial_tactical_tasks)
        return created


@router.post("/conversational-init")
async def api_conversational_project_init(request: Request, body: ConversationalInitBody):
    ctx = await build_project_tenant_context_from_request(
        request,
        route_operation="POST /api/projects/conversational-init",
    )
    validate_body_tenant_matches_context(body, ctx)
    assert_can_create_project(ctx)
    org_id = _org_from_ctx(ctx)
    project_slug = slugify_project_name(body.name)

    async with measure(subsystem="projects", operation="POST /api/projects/conversational-init"):
        created = await create_project(
            org_id,
            name=body.name,
            description=body.description or body.software_description[:8000],
            status=body.status,
        )
        project_id = uuid.UUID(created["id"])
        await initialize_project_setup(
            org_id,
            project_id,
            location_base=body.location_base,
            key_contacts=body.key_contacts,
            initial_tactical_tasks=body.initial_tactical_tasks,
        )
        await _seed_initial_tactical_tasks(org_id, project_id, body.initial_tactical_tasks)

        create_project_directory(project_slug)
        explicit_tables = (
            [table.model_dump() for table in body.schema_tables] if body.schema_tables else None
        )
        try:
            schema_payload = provision_conversational_workspace_schema(
                project_slug,
                body.software_description,
                explicit_tables,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        matrix = await load_project_memory(org_id, project_id)
        matrix["jit_schema_blueprint"] = schema_payload["schema_blueprint"]
        matrix["software_description"] = body.software_description.strip()[:16000]
        await save_project_memory(org_id, project_id, matrix)

        return {
            **created,
            "project_slug": schema_payload["project_slug"],
            "schema_blueprint": schema_payload["schema_blueprint"],
            "tables_created": schema_payload["tables_created"],
            "software_description": body.software_description.strip(),
        }


@router.get("/{project_id}")
async def api_get_project(request: Request, project_id: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects/{id}"
    )
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="GET /api/projects/{id}"):
        return await get_project(_org_from_ctx(ctx), pid)


# --- Members ---


@router.get("/{project_id}/members")
async def api_list_members(request: Request, project_id: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects/{id}/members"
    )
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="GET /api/projects/{id}/members"):
        return await list_project_members(_org_from_ctx(ctx), pid)


@router.post("/{project_id}/members")
async def api_add_member(request: Request, project_id: str, body: MemberCreateBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="POST /api/projects/{id}/members"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="POST /api/projects/{id}/members"):
        return await add_project_member(
            _org_from_ctx(ctx),
            pid,
            name=body.name,
            member_type=body.member_type,
            role=body.role,
            hourly_rate=body.hourly_rate,
            email=body.email,
            phone=body.phone,
            contact_notes=body.contact_notes,
        )


@router.get("/{project_id}/members/{member_id}")
async def api_get_member(request: Request, project_id: str, member_id: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects/{id}/members/{member_id}"
    )
    pid = _parse_uuid(project_id, field="project_id")
    mid = _parse_uuid(member_id, field="member_id")
    async with measure(subsystem="projects", operation="GET /api/projects/{id}/members/{member_id}"):
        return await get_project_member(_org_from_ctx(ctx), pid, mid)


@router.patch("/{project_id}/members/{member_id}")
async def api_update_member(request: Request, project_id: str, member_id: str, body: MemberUpdateBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="PATCH /api/projects/{id}/members/{member_id}"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    mid = _parse_uuid(member_id, field="member_id")
    async with measure(subsystem="projects", operation="PATCH /api/projects/{id}/members/{member_id}"):
        return await update_project_member(
            _org_from_ctx(ctx),
            pid,
            mid,
            name=body.name,
            member_type=body.member_type,
            role=body.role,
            hourly_rate=body.hourly_rate,
            email=body.email,
            phone=body.phone,
            contact_notes=body.contact_notes,
        )


# --- Tasks ---


@router.get("/{project_id}/tasks")
async def api_list_tasks(request: Request, project_id: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects/{id}/tasks"
    )
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="GET /api/projects/{id}/tasks"):
        return await list_project_tasks(_org_from_ctx(ctx), pid)


@router.post("/{project_id}/tasks")
async def api_create_task(request: Request, project_id: str, body: TaskCreateBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="POST /api/projects/{id}/tasks"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    assigned = _parse_uuid(body.assigned_to, field="assigned_to") if body.assigned_to else None
    async with measure(subsystem="projects", operation="POST /api/projects/{id}/tasks"):
        return await create_project_task(
            _org_from_ctx(ctx),
            pid,
            title=body.title,
            description=body.description,
            status=body.status,
            priority=body.priority,
            due_date=body.due_date,
            assigned_to=assigned,
        )


@router.get("/{project_id}/tasks/{task_id}")
async def api_get_task(request: Request, project_id: str, task_id: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects/{id}/tasks/{task_id}"
    )
    pid = _parse_uuid(project_id, field="project_id")
    tid = _parse_uuid(task_id, field="task_id")
    async with measure(subsystem="projects", operation="GET /api/projects/{id}/tasks/{task_id}"):
        return await get_project_task(_org_from_ctx(ctx), pid, tid)


@router.patch("/{project_id}/tasks/{task_id}")
async def api_update_task(request: Request, project_id: str, task_id: str, body: TaskUpdateBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="PATCH /api/projects/{id}/tasks/{task_id}"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    tid = _parse_uuid(task_id, field="task_id")
    assigned = _parse_uuid(body.assigned_to, field="assigned_to") if body.assigned_to else None
    async with measure(subsystem="projects", operation="PATCH /api/projects/{id}/tasks/{task_id}"):
        return await update_project_task(
            _org_from_ctx(ctx),
            pid,
            tid,
            title=body.title,
            description=body.description,
            status=body.status,
            priority=body.priority,
            due_date=body.due_date,
            assigned_to=assigned,
            clear_assigned_to=body.clear_assigned_to,
        )


# --- Ledger ---


@router.get("/{project_id}/ledger")
async def api_list_ledger(request: Request, project_id: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects/{id}/ledger"
    )
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="GET /api/projects/{id}/ledger"):
        return await list_ledger_entries(_org_from_ctx(ctx), pid)


@router.post("/{project_id}/ledger")
async def api_create_ledger(request: Request, project_id: str, body: LedgerCreateBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="POST /api/projects/{id}/ledger"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="POST /api/projects/{id}/ledger"):
        return await create_ledger_entry(
            _org_from_ctx(ctx),
            pid,
            entry_type=body.entry_type,
            amount=body.amount,
            currency=body.currency,
            description=body.description,
            status=body.status,
            due_date=body.due_date,
        )


@router.get("/{project_id}/ledger/{entry_id}")
async def api_get_ledger(request: Request, project_id: str, entry_id: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects/{id}/ledger/{entry_id}"
    )
    pid = _parse_uuid(project_id, field="project_id")
    eid = _parse_uuid(entry_id, field="entry_id")
    async with measure(subsystem="projects", operation="GET /api/projects/{id}/ledger/{entry_id}"):
        return await get_ledger_entry(_org_from_ctx(ctx), pid, eid)


@router.patch("/{project_id}/ledger/{entry_id}")
async def api_update_ledger(request: Request, project_id: str, entry_id: str, body: LedgerUpdateBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="PATCH /api/projects/{id}/ledger/{entry_id}"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    eid = _parse_uuid(entry_id, field="entry_id")
    async with measure(subsystem="projects", operation="PATCH /api/projects/{id}/ledger/{entry_id}"):
        return await update_ledger_entry(
            _org_from_ctx(ctx),
            pid,
            eid,
            entry_type=body.entry_type,
            amount=body.amount,
            currency=body.currency,
            description=body.description,
            status=body.status,
            due_date=body.due_date,
        )


# --- Native tools (invoice capture, accounting export, LLM function calling) ---


@router.get("/{project_id}/tools")
async def api_list_native_tools(request: Request, project_id: str):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="GET /api/projects/{id}/tools"
    )
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="GET /api/projects/{id}/tools"):
        await get_project(_org_from_ctx(ctx), pid)
        return {"tools": NATIVE_TOOL_DEFINITIONS, "project_id": str(pid)}


@router.post("/{project_id}/credit-memos/capture")
async def api_capture_credit_memo(request: Request, project_id: str, body: InvoiceCaptureBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="POST /api/projects/{id}/credit-memos/capture"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="POST /api/projects/{id}/credit-memos/capture"):
        return await process_credit_memo(
            _org_from_ctx(ctx),
            pid,
            file_path=body.file_path,
            image_url=body.image_url,
            filename=body.filename,
            vendor_hint=body.vendor_hint,
            amount_hint=body.amount_hint,
            currency_hint=body.currency_hint,
        )


@router.post("/{project_id}/invoices/capture")
async def api_capture_invoice(request: Request, project_id: str, body: InvoiceCaptureBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="POST /api/projects/{id}/invoices/capture"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="POST /api/projects/{id}/invoices/capture"):
        return await process_captured_invoice(
            _org_from_ctx(ctx),
            pid,
            file_path=body.file_path,
            image_url=body.image_url,
            filename=body.filename,
            vendor_hint=body.vendor_hint,
            amount_hint=body.amount_hint,
            currency_hint=body.currency_hint,
        )


@router.post("/{project_id}/ledger/export")
async def api_export_ledger(request: Request, project_id: str, body: LedgerExportBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="POST /api/projects/{id}/ledger/export"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="POST /api/projects/{id}/ledger/export"):
        return await export_ledger_to_accountant(
            _org_from_ctx(ctx),
            pid,
            format=body.format,
        )


@router.post("/{project_id}/tools/execute")
async def api_execute_native_tool(request: Request, project_id: str, body: NativeToolExecuteBody):
    ctx = await build_project_tenant_context_from_request(
        request, route_operation="POST /api/projects/{id}/tools/execute"
    )
    validate_body_tenant_matches_context(body, ctx)
    pid = _parse_uuid(project_id, field="project_id")
    async with measure(subsystem="projects", operation="POST /api/projects/{id}/tools/execute"):
        return await execute_native_tool(
            body.tool_name,
            body.arguments,
            org_id=_org_from_ctx(ctx),
            project_id=pid,
        )
