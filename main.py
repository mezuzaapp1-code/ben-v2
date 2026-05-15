from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.chat_service import handle_chat
from services.council_service import run_council
from services.health_service import build_health_payload, build_ready_payload

app = FastAPI()
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
    payload, status_code = await build_health_payload()
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/ready")
async def ready():
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
    return await run_council(body.question, body.tenant_id)
