from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from pydantic import BaseModel, Field

from services.chat_service import handle_chat

app = FastAPI()


class ChatBody(BaseModel):
    message: str
    tenant_id: str = Field(..., description="UUID string for org / RLS")
    tier: str = "free"


@app.post("/chat")
async def chat(body: ChatBody):
    return await handle_chat(body.message, "anonymous", body.tenant_id, body.tier)
