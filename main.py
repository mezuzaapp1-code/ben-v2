from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from services.chat_service import handle_chat

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


class ChatBody(BaseModel):
    message: str
    tenant_id: str = Field(..., description="UUID string for org / RLS")
    tier: str = "free"


@app.post("/chat")
async def chat(body: ChatBody):
    return await handle_chat(body.message, "anonymous", body.tenant_id, body.tier)
