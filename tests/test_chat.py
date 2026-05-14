"""Smoke: imports only — no API or database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://smoke:smoke@127.0.0.1:65432/smoke")

from services.chat_service import handle_chat
from services.model_gateway import route_request

print("All imports OK")
