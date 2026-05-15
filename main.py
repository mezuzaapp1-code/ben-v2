from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import uuid

from fastapi import FastAPI, Request
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


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign request_id for traced routes."""

    _TRACED = frozenset({"/chat", "/council", "/health", "/ready"})

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._TRACED:
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
    tenant_id: str | None = Field(
        None,
        description="Optional; ignored when unsigned. If signed, must match JWT org or omitted.",
    )
    tier: str = "free"


@app.post("/chat")
async def chat(request: Request, body: ChatBody):
    outcome, claims, auth_present = await apply_auth_policy(request, route_operation="POST /chat")
    ctx = build_tenant_context(outcome, claims, auth_present)
    validate_body_tenant_matches_context(body, ctx)
    log_tenant_bound(route_operation="POST /chat", ctx=ctx)
    return await handle_chat(body.message, ctx.user_id or "anonymous", ctx.org_id, body.tier)


class CouncilBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    tenant_id: str | None = Field(
        None,
        description="Optional; ignored when unsigned. If signed, must match JWT org or omitted.",
    )


@app.post("/council")
async def council(request: Request, body: CouncilBody):
    outcome, claims, auth_present = await apply_auth_policy(request, route_operation="POST /council")
    ctx = build_tenant_context(outcome, claims, auth_present)
    validate_body_tenant_matches_context(body, ctx)
    log_tenant_bound(route_operation="POST /council", ctx=ctx)
    async with measure(subsystem="council", operation="POST /council"):
        return await run_council(body.question, ctx.org_id)
