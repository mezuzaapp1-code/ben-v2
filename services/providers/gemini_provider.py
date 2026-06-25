"""Google Gemini generateContent adapter."""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from services.chat_prompt import GLOBAL_CHAT_SYSTEM
from services.providers.base_provider import BaseProvider, ProviderSendResult, tenant_header

# May 2026 frontier default (enforced when callers omit explicit model env overrides).
GEMINI_FAST_MODEL = "gemini-3.5-flash"


class GeminiProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "google"

    def _payload(self, message: str, system: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"contents": [{"parts": [{"text": message}]}]}
        sys_text = (system or GLOBAL_CHAT_SYSTEM).strip()
        if sys_text:
            payload["systemInstruction"] = {"parts": [{"text": sys_text}]}
        return payload

    async def send_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> ProviderSendResult:
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = await cx.post(
            url,
            params={"key": api_key},
            headers=tenant_header(tenant_id),
            json=self._payload(message, system),
        )
        r.raise_for_status()
        d = r.json()
        parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        txt = "".join(p.get("text", "") for p in parts)
        m = d.get("usageMetadata") or {}
        pi, po = int(m.get("promptTokenCount", 0)), int(m.get("candidatesTokenCount", 0))
        return ProviderSendResult.from_token_counts(txt, pi, po)

    async def stream_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"
        async with cx.stream(
            "POST",
            url,
            params={"key": api_key, "alt": "sse"},
            headers=tenant_header(tenant_id),
            json=self._payload(message, system),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                candidates = data.get("candidates") or []
                if not candidates:
                    continue
                parts = ((candidates[0].get("content") or {}).get("parts") or [])
                for part in parts:
                    text = part.get("text")
                    if text:
                        yield str(text)
