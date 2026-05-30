"""OpenAI chat completions adapter."""
from __future__ import annotations

import os

import httpx

from services.providers.base_provider import BaseProvider, ProviderSendResult, tenant_header


class OpenAIProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "openai"

    async def send_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
    ) -> ProviderSendResult:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        r = await cx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", **tenant_header(tenant_id)},
            json={"model": model, "messages": [{"role": "user", "content": message}]},
        )
        r.raise_for_status()
        d = r.json()
        u = d.get("usage") or {}
        pi, po = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        return str(d["choices"][0]["message"]["content"]), pi + po, pi, po
