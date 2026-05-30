from contextlib import asynccontextmanager



from dotenv import load_dotenv



load_dotenv()



import uuid



from fastapi import FastAPI, HTTPException, Request

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse, StreamingResponse

from pydantic import BaseModel, ConfigDict, Field

from starlette.middleware.base import BaseHTTPMiddleware



from auth.shadow_auth import apply_auth_policy

from auth.tenant_binding import build_tenant_context, log_tenant_bound, validate_body_tenant_matches_context

from services.chat_language import normalize_language_code
from services.chat_service import handle_chat
from services.model_gateway import normalize_chat_provider_id

from services.council_service import CouncilTranscriptPersistError, run_council, stream_council_response

from services.health_service import build_health_payload, build_ready_payload

from services.ops.logging_config import configure_ben_ops_logging

from services.ops.request_context import attach_request_id, get_request_id, set_request_id

from services.ops.startup import validate_startup

from services.ops.load_governance import get_load_governor, locale_for_request

from services.ops.idempotency import (
    CLIENT_REQUEST_ID_HEADER,
    get_idempotency_registry,
    resolve_client_request_id,
)
from services.ops.runtime_diagnostics import (
    begin_request_diagnostics,
    build_runtime_snapshot,
    complete_request_diagnostics,
    fail_request_diagnostics,
)
from services.ops.runtime_state import finalize_chat_payload, finalize_council_payload

from services.ops.timing import measure

from services.adhoc_council_service import run_adhoc_expert, run_adhoc_synthesize
from services.continuity_service import build_thread_continuity
from services.thread_service import get_thread_detail, list_threads





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

    })



    async def dispatch(self, request: Request, call_next):

        path = request.url.path

        if path in self._TRACED or path.startswith("/api/threads/"):

            incoming = request.headers.get("X-Request-ID", "").strip()

            set_request_id(incoming if incoming else str(uuid.uuid4()))

        response = await call_next(request)

        return response





@asynccontextmanager

async def lifespan(app: FastAPI):

    configure_ben_ops_logging()

    validate_startup()

    yield





app = FastAPI(lifespan=lifespan)

app.add_middleware(RequestIdMiddleware)

app.add_middleware(

    CORSMiddleware,

    allow_origins=[

        "http://localhost:5173",

        "https://ben-v2.vercel.app",

        "https://*.vercel.app",

    ],

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

    log_tenant_bound(route_operation=route_operation, ctx=ctx)

    return ctx





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

    locale = locale_for_request(request, body.message)

    client_rid = resolve_client_request_id(
        body_value=body.client_request_id,
        header_value=request.headers.get(CLIENT_REQUEST_ID_HEADER),
    )

    begin_request_diagnostics(route="/chat", ctx=ctx, text_hint=body.message)

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


_COUNCIL_STREAM_EXPERTS = ("Legal Advisor", "Business Advisor", "Strategy Advisor")


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

                raw = await run_council(body.question, ctx.tenant_id, thread_id=tid)

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

    return StreamingResponse(
        stream_council_response(
            body.question,
            list(_COUNCIL_STREAM_EXPERTS),
            ctx.tenant_id,
            thread_id=tid,
        ),
        media_type="application/x-ndjson",
    )


@app.get("/runtime/snapshot")

async def runtime_snapshot():

    """Safe operational metrics (no secrets, no tenant PII, no prompts)."""

    snap = await build_runtime_snapshot()

    return attach_request_id(snap)





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
    client_request_id: str | None = Field(
        None,
        max_length=128,
        description="Client-generated idempotency token for safe retries",
    )


class AdhocSynthesizeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="UUID grouping this ad-hoc round")
    mode: str = Field(
        "consensus",
        description="consensus (requires 2+ AI voices) or single_voice_wrap",
    )
    client_request_id: str | None = Field(
        None,
        max_length=128,
        description="Client-generated idempotency token for safe retries",
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
    org_id = uuid.UUID(ctx.tenant_id)
    async with measure(subsystem="adhoc", operation="POST /api/threads/{id}/adhoc/expert"):
        return await run_adhoc_expert(
            org_id,
            tid,
            session_id=sid,
            provider_id=provider_id,
            tenant_id=ctx.tenant_id,
            tier=body.tier,
        )


@app.post("/api/threads/{thread_id}/adhoc/synthesize")
async def api_adhoc_synthesize(request: Request, thread_id: str, body: AdhocSynthesizeBody):
    ctx = await _tenant_ctx_from_request(
        request, route_operation="POST /api/threads/{id}/adhoc/synthesize"
    )
    tid = _parse_thread_id(thread_id)
    if tid is None:
        raise HTTPException(422, "Invalid thread_id")
    sid = _parse_required_uuid(body.session_id, field="session_id")
    mode_raw = (body.mode or "consensus").strip().lower()
    if mode_raw not in ("consensus", "single_voice_wrap"):
        raise HTTPException(
            422,
            detail="mode must be consensus or single_voice_wrap",
        )
    org_id = uuid.UUID(ctx.tenant_id)
    async with measure(subsystem="adhoc", operation="POST /api/threads/{id}/adhoc/synthesize"):
        return await run_adhoc_synthesize(
            org_id,
            tid,
            session_id=sid,
            tenant_id=ctx.tenant_id,
            mode=mode_raw,  # type: ignore[arg-type]
        )


