"""Google Gemini generateContent adapter."""
from __future__ import annotations

import os

import httpx

from services.providers.base_provider import BaseProvider, ProviderSendResult, tenant_header


class GeminiProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "google"

    async def send_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
    ) -> ProviderSendResult:
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = await cx.post(
            url,
            params={"key": api_key},
            headers=tenant_header(tenant_id),
            json={"contents": [{"parts": [{"text": message}]}]},
        )
        r.raise_for_status()
        d = r.json()
        parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        txt = "".join(p.get("text", "") for p in parts)
        m = d.get("usageMetadata") or {}
        pi, po = int(m.get("promptTokenCount", 0)), int(m.get("candidatesTokenCount", 0))
        return txt, pi + po, pi, po
