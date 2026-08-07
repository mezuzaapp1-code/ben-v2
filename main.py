from contextlib import asynccontextmanager



from dotenv import load_dotenv



load_dotenv()



import uuid



from fastapi import FastAPI, HTTPException, Request

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse, StreamingResponse

from pydantic import BaseModel, ConfigDict, Field

from starlette.middleware.base import BaseHTTPMiddleware



from auth.beta_gate import extract_beta_feedback_meta, maybe_beta_auditor_context
from auth.shadow_auth import apply_auth_policy

from auth.tenant_binding import TenantContext, build_tenant_context, log_tenant_bound, validate_body_tenant_matches_context

from services.chat_intent import apply_chat_intent_to_request
from services.chat_language import normalize_language_code
from services.chat_service import handle_chat, stream_chat_response
from services.model_gateway import (
    normalize_chat_provider_id,
    normalize_model_override,
    validate_chat_model_override,
)

from services.council_service import (
    CouncilTranscriptPersistError,
    run_council,
    stream_council_response,
)

from services.health_service import build_health_payload, build_ready_payload

from services.ops.logging_config import configure_ben_ops_logging

from services.ops.request_context import attach_request_id, get_request_id, set_request_id

from database.connection import warmup_database_pool
from services.ops.startup import validate_startup
from services.ops.structured_log import log_warning

from services.ops.load_governance import get_load_governor, locale_for_request

from services.ops.idempotency import (
    CLIENT_REQUEST_ID_HEADER,
    get_idempotency_registry,
    resolve_client_request_id,
)
from services.ops.runtime_diagnostics import (
    attach_execution_plan_to_request_diagnostics,
    attach_workspace_to_request_diagnostics,
    begin_request_diagnostics,
    build_runtime_snapshot,
    complete_request_diagnostics,
    fail_request_diagnostics,
)
from services.ops.runtime_state import finalize_chat_payload, finalize_council_payload

from services.ops.timing import measure

from services.adhoc_council_service import run_adhoc_expert, stream_adhoc_expert
from services.expert_opinion_service import run_expert_opinion, stream_expert_opinion
from services.continuity_service import build_thread_continuity
from services.feedback_capture_service import capture_beta_feedback
from services.thread_service import (
    create_project_workspace_thread,
    delete_thread,
    get_thread_detail,
    list_threads,
    promote_thread_to_project,
)
from services.workspace_resolver import CLIENT_WORKSPACE_ID_HEADER, resolve_workspace_context
from services.execution_plan import resolve_execution_plan
from services.inference.execution_context import begin_execution_context
from routers.beta_session import router as beta_session_router
from database.knowledge_store import init_knowledge_store
from database.thread_store import init_thread_store
from routers.knowledge import project_knowledge_router, router as knowledge_router
from routers.platform_capabilities import router as platform_capabilities_router
from routers.repositories import router as project_repositories_router
from routers.workspace_files import (
    project_alias_router as workspace_files_project_router,
    router as workspace_files_router,
)
from routers.news_product import router as news_product_router
from routers.news_sources import router as news_sources_router
from routers.projects import router as projects_router
from routers.public_basalt import router as public_basalt_router





class RequestIdMiddleware(BaseHTTPMiddleware):

    """Assign request_id for traced routes."""



    _TRACED = frozenset({

        "/chat",

        "/council",

        "/council/stream",

        "/health",

        "/ready",

        "/runtime/snapshot",

        "/api/threads",

        "/api/projects",

        "/api/public/basalt",

    })



    async def dispatch(self, request: Request, call_next):

        path = request.url.path

        if (
            path in self._TRACED
            or path.startswith("/api/threads/")
            or path.startswith("/api/projects")
            or path.startswith("/api/public/basalt")
        ):

            incoming = request.headers.get("X-Request-ID", "").strip()

            set_request_id(incoming if incoming else str(uuid.uuid4()))

        response = await call_next(request)

        return response





@asynccontextmanager

async def lifespan(app: FastAPI):

    configure_ben_ops_logging()

    validate_startup()
    init_knowledge_store()
    init_thread_store()

    if not await warmup_database_pool():
        log_warning(
            "database pool warmup failed at startup",
            subsystem="startup",
            provider="database",
            category="provider_unavailable",
            operation="db_warmup",
            outcome="error",
        )

    yield





app = FastAPI(lifespan=lifespan)

app.include_router(projects_router)
app.include_router(workspace_files_router)
app.include_router(workspace_files_project_router)
app.include_router(knowledge_router)
app.include_router(project_knowledge_router)
app.include_router(project_repositories_router)
app.include_router(platform_capabilities_router)
app.include_router(beta_session_router)
app.include_router(public_basalt_router)
app.include_router(news_product_router)
app.include_router(news_sources_router)

app.add_middleware(RequestIdMiddleware)

app.add_middleware(

    CORSMiddleware,

    allow_origins=[

        "http://localhost:5173",

        "http://127.0.0.1:5173",

        "https://ben-v2.vercel.app",

        "https://www.basalt.co.il",

        "https://basalt.co.il",

    ],

    allow_origin_regex=r"https://.*\.ngrok-free\.app|https://.*\.ngrok-free\.dev|https://.*\.ngrok\.io|https://.*\.ngrok\.app",

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)





def _parse_thread_id(raw: str | None) -> uuid.UUID | None:

    if raw is None or not str(raw).strip():

        return None

    try:

        return uuid.UUID(str(raw).strip())

    except ValueError as e:

        raise HTTPException(422, "Invalid thread_id") from e


def _parse_required_uuid(raw: str, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw).strip())
    except ValueError as e:
        raise HTTPException(422, f"Invalid {field}") from e


def _client_workspace_id_from_request(request: Request) -> str | None:
    raw = request.headers.get(CLIENT_WORKSPACE_ID_HEADER)
    if raw and str(raw).strip():
        return str(raw).strip()
    alt = request.headers.get("X-Workspace-Id")
    if alt and str(alt).strip():
        return str(alt).strip()
    return None


def _attach_request_workspace_context(
    ctx: TenantContext,
    request: Request,
    *,
    thread_id: uuid.UUID | None = None,
    project_id: str | None = None,
    project_slug: str | None = None,
):
    workspace_ctx = resolve_workspace_context(
        ctx,
        thread_id=str(thread_id) if thread_id else None,
        project_id=project_id,
        project_slug=project_slug,
        client_workspace_id=_client_workspace_id_from_request(request),
    )
    attach_workspace_to_request_diagnostics(workspace_ctx)
    return workspace_ctx


def _attach_request_execution_plan(
    workspace_ctx,
    *,
    capability_key: str,
    requested_resource: str | None = None,
):
    plan = resolve_execution_plan(
        workspace_ctx,
        capability_key,
        requested_resource=requested_resource,
    )
    attach_execution_plan_to_request_diagnostics(plan)
    # Pass 1: boarding-pass ExecutionContext for gateway accounting (measure-only).
    begin_execution_context(
        org_id=plan.org_id,
        workspace_id=plan.workspace_id,
        capability_key=plan.capability_key,
        pipeline=plan.capability_key,
        provider=plan.resolved_resource,
    )
    return plan


def _enforce_chat_execution_plan(plan) -> None:
    """Phase 1: Switchboard compute activation must not kill chat.

    ExecutionPlan is still attached for diagnostics/telemetry. Inactive
    org_switchboard state is informational only on /chat and /chat/stream.
    """
    return





def _fail_request_from_http(exc: HTTPException, *, route: str) -> None:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = detail.get("code") if isinstance(detail, dict) else None
    if code in (
        "council_busy",
        "runtime_saturated",
        "retry_later",
        "duplicate_request",
        "idempotency_rejected",
    ):
        fail_request_diagnostics(outcome="rejected", category=str(code), route=route)
    else:
        fail_request_diagnostics(outcome="error", category=str(code) if code else None, route=route)


async def _tenant_ctx_from_request(request: Request, *, route_operation: str):

    outcome, claims, auth_present = await apply_auth_policy(request, route_operation=route_operation)

    ctx = build_tenant_context(outcome, claims, auth_present)

    beta_ctx = maybe_beta_auditor_context(request)
    if beta_ctx:
        log_tenant_bound(route_operation=route_operation, ctx=beta_ctx)
        return beta_ctx

    log_tenant_bound(route_operation=route_operation, ctx=ctx)

    return ctx


async def _capture_chat_feedback_if_beta(request: Request, message: str, *, route: str) -> None:
    meta = extract_beta_feedback_meta(request)
    if not meta:
        return
    await capture_beta_feedback(message=message, route=route, **meta)





@app.get("/health")

async def health():

    async with measure(subsystem="health", operation="GET /health"):

        payload, status_code = await build_health_payload()

    return JSONResponse(content=payload, status_code=status_code)





@app.get("/ready")

async def ready():

    async with measure(subsystem="ready", operation="GET /ready"):

        payload, status_code = await build_ready_payload()

    return JSONResponse(content=payload, status_code=status_code)





class ChatBody(BaseModel):

    model_config = ConfigDict(extra="forbid")



    message: str

    thread_id: str | None = Field(None, description="Continue an existing thread when set")

    tenant_id: str | None = Field(

        None,

        description="Optional; ignored when unsigned. If signed, must match JWT org or omitted.",

    )

    tier: str = "free"

    provider_id: str | None = Field(
        None,
        description="Speaking provider for chat routing: gpt, claude, or gemini",
    )

    preferred_language: str | None = Field(
        None,
        description="Response language for this message: en or he",
    )

    client_request_id: str | None = Field(
        None,
        max_length=128,
        description="Client-generated idempotency token for safe retries",
    )

    expert_opinion: bool = Field(
        False,
        description="Rolling context mode: append all prior thread turns before streaming",
    )

    model_override: str | None = Field(
        None,
        max_length=128,
        description="Canonical BEN model id from the frontier allowlist; resolved to provider API id at dispatch",
    )

    project_id: str | None = Field(
        None,
        description="Active project UUID for copilot tool execution and mutated_state cards",
    )

    project_setup_bootstrap: bool = Field(
        False,
        description="Hidden first-turn bootstrap for interactive project workspace onboarding",
    )





@app.post("/chat")

async def chat(request: Request, body: ChatBody):

    ctx = await _tenant_ctx_from_request(request, route_operation="POST /chat")

    validate_body_tenant_matches_context(body, ctx)

    tid = _parse_thread_id(body.thread_id)

    try:
        chat_provider_id = normalize_chat_provider_id(body.provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        chat_preferred_language = normalize_language_code(body.preferred_language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        chat_model_override = normalize_model_override(body.model_override)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        validate_chat_model_override(chat_provider_id, chat_model_override)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    locale = locale_for_request(request, body.message)

    client_rid = resolve_client_request_id(
        body_value=body.client_request_id,
        header_value=request.headers.get(CLIENT_REQUEST_ID_HEADER),
    )

    begin_request_diagnostics(route="/chat", ctx=ctx, text_hint=body.message)
    workspace_ctx = _attach_request_workspace_context(
        ctx,
        request,
        thread_id=tid,
        project_id=body.project_id,
    )
    plan = _attach_request_execution_plan(
        workspace_ctx,
        capability_key="standard_chat",
        requested_resource=chat_model_override or chat_provider_id,
    )
    _enforce_chat_execution_plan(plan)

    await _capture_chat_feedback_if_beta(request, body.message, route="/chat")

    idem = await get_idempotency_registry().begin(
        route="/chat",
        tenant_id=ctx.tenant_id,
        client_request_id=client_rid,
    )

    if not idem.active and idem.replay_response is not None:
        result = await finalize_chat_payload(
            idem.replay_response,
            client_request_id=client_rid,
            idempotent_replay=True,
        )
        complete_request_diagnostics(outcome="replay")
        return result

    try:

        async with get_load_governor().govern_chat(locale=locale):

            raw = await handle_chat(

                body.message,

                ctx.user_id or "anonymous",

                ctx.tenant_id,

                body.tier,

                thread_id=tid,

                provider_id=chat_provider_id,

                model_override=chat_model_override,

                preferred_language=chat_preferred_language,

            )

        result = await finalize_chat_payload(raw, client_request_id=client_rid)

        await get_idempotency_registry().complete(idem.store_key, result)

        complete_request_diagnostics(outcome="ok")

        return result

    except HTTPException as exc:

        await get_idempotency_registry().fail(idem.store_key)

        _fail_request_from_http(exc, route="/chat")

        raise

    except Exception:

        await get_idempotency_registry().fail(idem.store_key)

        fail_request_diagnostics(outcome="error")

        raise





@app.post("/chat/stream")
async def chat_stream(request: Request, body: ChatBody):
    ctx = await _tenant_ctx_from_request(request, route_operation="POST /chat/stream")
    validate_body_tenant_matches_context(body, ctx)
    tid = _parse_thread_id(body.thread_id)
    try:
        chat_provider_id = normalize_chat_provider_id(body.provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        chat_preferred_language = normalize_language_code(body.preferred_language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        chat_model_override = normalize_model_override(body.model_override)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        validate_chat_model_override(chat_provider_id, chat_model_override)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    resolved_provider_id, resolved_model_override, resolved_expert_opinion = apply_chat_intent_to_request(
        body.message,
        provider_id=chat_provider_id,
        model_override=chat_model_override,
        expert_opinion=bool(body.expert_opinion),
    )
    begin_request_diagnostics(route="/chat/stream", ctx=ctx, text_hint=body.message)
    workspace_ctx = _attach_request_workspace_context(
        ctx,
        request,
        thread_id=tid,
        project_id=body.project_id,
    )
    plan = _attach_request_execution_plan(
        workspace_ctx,
        capability_key="standard_chat",
        requested_resource=resolved_model_override or resolved_provider_id,
    )
    _enforce_chat_execution_plan(plan)
    await _capture_chat_feedback_if_beta(request, body.message, route="/chat/stream")
    # Conditional tool injection: llm_tools_for_thread_session returns schemas only for
    # project_setup threads (system_main.db). Regular chats stay tool-free in chat_service.
    copilot_project_id = None
    if body.project_id:
        try:
            copilot_project_id = uuid.UUID(str(body.project_id).strip())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid project_id") from exc
    return StreamingResponse(
        stream_chat_response(
            body.message,
            ctx.user_id or "anonymous",
            ctx.tenant_id,
            body.tier,
            thread_id=tid,
            provider_id=resolved_provider_id,
            model_override=resolved_model_override,
            preferred_language=chat_preferred_language,
            expert_opinion=resolved_expert_opinion,
            project_id=copilot_project_id,
            project_setup_bootstrap=bool(body.project_setup_bootstrap),
        ),
        media_type="application/x-ndjson",
    )





class CouncilBody(BaseModel):

    model_config = ConfigDict(extra="forbid")



    question: str

    thread_id: str | None = Field(None, description="Persist council transcript to this thread when set")

    tenant_id: str | None = Field(

        None,

        description="Optional; ignored when unsigned. If signed, must match JWT org or omitted.",

    )

    client_request_id: str | None = Field(
        None,
        max_length=128,
        description="Client-generated idempotency token for safe retries",
    )

    force_codebase: bool | None = Field(
        None,
        description="Force Local Codebase Expert lane",
    )


@app.post("/council")

async def council(request: Request, body: CouncilBody):

    ctx = await _tenant_ctx_from_request(request, route_operation="POST /council")

    validate_body_tenant_matches_context(body, ctx)

    tid = _parse_thread_id(body.thread_id)

    locale = locale_for_request(request, body.question)

    client_rid = resolve_client_request_id(
        body_value=body.client_request_id,
        header_value=request.headers.get(CLIENT_REQUEST_ID_HEADER),
    )

    begin_request_diagnostics(route="/council", ctx=ctx, text_hint=body.question)
    workspace_ctx = _attach_request_workspace_context(ctx, request, thread_id=tid)
    _attach_request_execution_plan(workspace_ctx, capability_key="council")

    idem = await get_idempotency_registry().begin(
        route="/council",
        tenant_id=ctx.tenant_id,
        client_request_id=client_rid,
    )

    if not idem.active and idem.replay_response is not None:
        result = await finalize_council_payload(
            idem.replay_response,
            client_request_id=client_rid,
            idempotent_replay=True,
        )
        complete_request_diagnostics(outcome="replay")
        return result

    try:

        async with get_load_governor().govern_council(

            tenant_id=ctx.tenant_id,

            question=body.question,

            locale=locale,

        ):

            async with measure(subsystem="council", operation="POST /council"):
                raw = await run_council(
                    body.question,
                    ctx.tenant_id,
                    thread_id=tid,
                    force_codebase=bool(body.force_codebase),
                )

        result = await finalize_council_payload(raw, client_request_id=client_rid)

        await get_idempotency_registry().complete(idem.store_key, result)

        complete_request_diagnostics(outcome="ok")

        return result

    except CouncilTranscriptPersistError:

        await get_idempotency_registry().fail(idem.store_key)

        fail_request_diagnostics(outcome="persistence_failed", route="/council")

        body = {
            "error": "council_persistence_failed",
            "message": "Council completed but transcript persistence failed. Please retry.",
            "retryable": True,
        }
        rid = get_request_id()
        if rid:
            body["request_id"] = rid
        return JSONResponse(status_code=503, content=body)

    except HTTPException as exc:

        await get_idempotency_registry().fail(idem.store_key)

        _fail_request_from_http(exc, route="/council")

        raise

    except Exception:

        await get_idempotency_registry().fail(idem.store_key)

        fail_request_diagnostics(outcome="error")

        raise


@app.post("/council/stream")
async def council_stream(request: Request, body: CouncilBody):
    ctx = await _tenant_ctx_from_request(request, route_operation="POST /council/stream")
    validate_body_tenant_matches_context(body, ctx)

    tid = _parse_thread_id(body.thread_id)

    begin_request_diagnostics(route="/council/stream", ctx=ctx, text_hint=body.question)
    workspace_ctx = _attach_request_workspace_context(ctx, request, thread_id=tid)
    _attach_request_execution_plan(workspace_ctx, capability_key="council")

    return StreamingResponse(
        stream_council_response(
            body.question,
            ctx.tenant_id,
            thread_id=tid,
            force_codebase=bool(body.force_codebase),
        ),
        media_type="application/x-ndjson",
    )


@app.get("/runtime/snapshot")

async def runtime_snapshot():

    """Safe operational metrics (no secrets, no tenant PII, no prompts)."""

    snap = await build_runtime_snapshot()

    return attach_request_id(snap)





class ProjectWorkspaceCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_slug: str | None = Field(None, min_length=1, max_length=64)
    title: str | None = Field(None, min_length=1, max_length=512)


@app.post("/api/threads/project-workspace")
async def api_create_project_workspace(
    request: Request,
    body: ProjectWorkspaceCreateBody | None = None,
):
    ctx = await _tenant_ctx_from_request(request, route_operation="POST /api/threads/project-workspace")
    payload = body or ProjectWorkspaceCreateBody()
    return await create_project_workspace_thread(
        uuid.UUID(ctx.tenant_id),
        project_slug=payload.project_slug,
        title=payload.title,
    )


@app.get("/api/threads")

async def api_list_threads(request: Request):

    ctx = await _tenant_ctx_from_request(request, route_operation="GET /api/threads")

    return await list_threads(uuid.UUID(ctx.tenant_id))





@app.get("/api/threads/{thread_id}")

async def api_get_thread(request: Request, thread_id: str):

    ctx = await _tenant_ctx_from_request(request, route_operation="GET /api/threads/{id}")

    tid = _parse_thread_id(thread_id)

    if tid is None:

        raise HTTPException(422, "Invalid thread_id")

    return await get_thread_detail(uuid.UUID(ctx.tenant_id), tid)


@app.delete("/api/threads/{thread_id}")
async def api_delete_thread(request: Request, thread_id: str):
    ctx = await _tenant_ctx_from_request(request, route_operation="DELETE /api/threads/{id}")
    tid = _parse_thread_id(thread_id)
    if tid is None:
        raise HTTPException(422, "Invalid thread_id")
    return await delete_thread(uuid.UUID(ctx.tenant_id), tid)


class PromoteThreadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_slug: str = Field(..., min_length=1, max_length=64, description="URL-safe project portfolio slug")


@app.post("/api/threads/{thread_id}/promote")
async def api_promote_thread(request: Request, thread_id: str, body: PromoteThreadBody):
    ctx = await _tenant_ctx_from_request(request, route_operation="POST /api/threads/{id}/promote")
    tid = _parse_thread_id(thread_id)
    if tid is None:
        raise HTTPException(422, "Invalid thread_id")
    return await promote_thread_to_project(
        uuid.UUID(ctx.tenant_id),
        tid,
        project_slug=body.project_slug.strip(),
    )


@app.get("/api/threads/{thread_id}/continuity")
async def api_thread_continuity(request: Request, thread_id: str):
    ctx = await _tenant_ctx_from_request(request, route_operation="GET /api/threads/{id}/continuity")
    tid = _parse_thread_id(thread_id)
    if tid is None:
        raise HTTPException(422, "Invalid thread_id")
    return await build_thread_continuity(uuid.UUID(ctx.tenant_id), tid)


class AdhocExpertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="UUID grouping this ad-hoc round")
    provider_id: str = Field(..., description="Speaking provider: gpt, claude, or gemini")
    tier: str = "free"
    anchor_message_id: int | None = Field(
        None,
        description="SQLite message id — rolling context includes turns up to this node",
    )
    opinion_mode: str = Field(
        "single",
        description="single = one guest expert; panel = panel discussion (stored as panel message_type)",
    )
    opinion_request: str | None = Field(
        None,
        description="Optional override for the expert opinion prompt",
    )
    client_request_id: str | None = Field(
        None,
        max_length=128,
        description="Client-generated idempotency token for safe retries",
    )


@app.post("/api/threads/{thread_id}/adhoc/expert/stream")
async def api_adhoc_expert_stream(request: Request, thread_id: str, body: AdhocExpertBody):
    ctx = await _tenant_ctx_from_request(
        request, route_operation="POST /api/threads/{id}/adhoc/expert/stream"
    )
    tid = _parse_thread_id(thread_id)
    if tid is None:
        raise HTTPException(422, "Invalid thread_id")
    sid = _parse_required_uuid(body.session_id, field="session_id")
    try:
        provider_id = normalize_chat_provider_id(body.provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if provider_id is None:
        raise HTTPException(status_code=400, detail="provider_id is required")
    # Gate 2: Switchboard compute activation must not block Add Opinion.
    org_id = uuid.UUID(ctx.tenant_id)
    message_type = "panel" if str(body.opinion_mode or "single").strip().lower() == "panel" else "expert_consult"
    stream_fn = stream_expert_opinion if body.anchor_message_id is not None else stream_adhoc_expert
    stream_kwargs: dict = {
        "session_id": sid,
        "provider_id": provider_id,
        "tenant_id": ctx.tenant_id,
        "tier": body.tier,
    }
    if body.anchor_message_id is not None:
        stream_kwargs.update(
            anchor_message_id=body.anchor_message_id,
            opinion_request=body.opinion_request,
            message_type=message_type,
        )
    return StreamingResponse(
        stream_fn(org_id, tid, **stream_kwargs),
        media_type="application/x-ndjson",
    )


@app.post("/api/threads/{thread_id}/adhoc/expert")
async def api_adhoc_expert(request: Request, thread_id: str, body: AdhocExpertBody):
    ctx = await _tenant_ctx_from_request(
        request, route_operation="POST /api/threads/{id}/adhoc/expert"
    )
    tid = _parse_thread_id(thread_id)
    if tid is None:
        raise HTTPException(422, "Invalid thread_id")
    sid = _parse_required_uuid(body.session_id, field="session_id")
    try:
        provider_id = normalize_chat_provider_id(body.provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if provider_id is None:
        raise HTTPException(status_code=400, detail="provider_id is required")
    # Gate 2: Switchboard compute activation must not block Add Opinion.
    org_id = uuid.UUID(ctx.tenant_id)
    message_type = "panel" if str(body.opinion_mode or "single").strip().lower() == "panel" else "expert_consult"
    async with measure(subsystem="adhoc", operation="POST /api/threads/{id}/adhoc/expert"):
        if body.anchor_message_id is not None:
            return await run_expert_opinion(
                org_id,
                tid,
                session_id=sid,
                provider_id=provider_id,
                tenant_id=ctx.tenant_id,
                tier=body.tier,
                anchor_message_id=body.anchor_message_id,
                opinion_request=body.opinion_request,
                message_type=message_type,
            )
        return await run_adhoc_expert(
            org_id,
            tid,
            session_id=sid,
            provider_id=provider_id,
            tenant_id=ctx.tenant_id,
            tier=body.tier,
        )


