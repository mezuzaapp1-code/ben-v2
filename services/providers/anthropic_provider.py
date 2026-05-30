"""Anthropic messages API adapter."""
from __future__ import annotations

import os

import httpx

from services.providers.base_provider import BaseProvider, ProviderSendResult, tenant_header


class AnthropicProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def send_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
    ) -> ProviderSendResult:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        r = await cx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                **tenant_header(tenant_id),
            },
            json={"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": message}]},
        )
        r.raise_for_status()
        d = r.json()
        txt = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        u = d.get("usage") or {}
        pi, po = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        return txt, pi + po, pi, po
