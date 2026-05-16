from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware

from auth.shadow_auth import apply_auth_policy
from auth.tenant_binding import build_tenant_context, log_tenant_bound, validate_body_tenant_matches_context
from services.chat_service import handle_chat
from services.council_service import run_council
from services.health_service import build_health_payload, build_ready_payload
from services.ops.logging_config import configure_ben_ops_logging
from services.ops.request_context import set_request_id
from services.ops.startup import validate_startup
from services.ops.timing import measure
from services.thread_service import get_thread_detail, list_threads


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign request_id for traced routes."""

    _TRACED = frozenset({
        "/chat",
        "/council",
        "/health",
        "/ready",
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


@app.post("/chat")
async def chat(request: Request, body: ChatBody):
    ctx = await _tenant_ctx_from_request(request, route_operation="POST /chat")
    validate_body_tenant_matches_context(body, ctx)
    tid = _parse_thread_id(body.thread_id)
    return await handle_chat(
        body.message,
        ctx.user_id or "anonymous",
        ctx.org_id,
        body.tier,
        thread_id=tid,
    )


class CouncilBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    thread_id: str | None = Field(None, description="Persist council transcript to this thread when set")
    tenant_id: str | None = Field(
        None,
        description="Optional; ignored when unsigned. If signed, must match JWT org or omitted.",
    )


@app.post("/council")
async def council(request: Request, body: CouncilBody):
    ctx = await _tenant_ctx_from_request(request, route_operation="POST /council")
    validate_body_tenant_matches_context(body, ctx)
    tid = _parse_thread_id(body.thread_id)
    async with measure(subsystem="council", operation="POST /council"):
        return await run_council(body.question, ctx.org_id, thread_id=tid)


@app.get("/api/threads")
async def api_list_threads(request: Request):
    ctx = await _tenant_ctx_from_request(request, route_operation="GET /api/threads")
    return await list_threads(uuid.UUID(ctx.org_id))


@app.get("/api/threads/{thread_id}")
async def api_get_thread(request: Request, thread_id: str):
    ctx = await _tenant_ctx_from_request(request, route_operation="GET /api/threads/{id}")
    tid = _parse_thread_id(thread_id)
    if tid is None:
        raise HTTPException(422, "Invalid thread_id")
    return await get_thread_detail(uuid.UUID(ctx.org_id), tid)
