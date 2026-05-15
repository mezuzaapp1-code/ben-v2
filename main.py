from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from services.chat_service import handle_chat
from services.council_service import run_council
from services.health_service import build_health_payload, build_ready_payload
from services.ops.logging_config import configure_ben_ops_logging
from services.ops.request_context import set_request_id
from services.ops.startup import validate_startup
from services.ops.timing import measure


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign request_id for traced routes."""

    _TRACED = frozenset({"/council", "/health", "/ready"})

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
    message: str
    tenant_id: str = Field(..., description="UUID string for org / RLS")
    tier: str = "free"


@app.post("/chat")
async def chat(body: ChatBody):
    return await handle_chat(body.message, "anonymous", body.tenant_id, body.tier)


class CouncilBody(BaseModel):
    question: str
    tenant_id: str = Field(..., description="UUID string for org / tracing")


@app.post("/council")
async def council(body: CouncilBody):
    async with measure(subsystem="council", operation="POST /council"):
        return await run_council(body.question, body.tenant_id)
