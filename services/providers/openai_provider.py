"""OpenAI chat completions adapter."""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx

from services.chat_prompt import GLOBAL_CHAT_SYSTEM
from services.providers.base_provider import BaseProvider, ProviderSendResult, tenant_header

# May 2026 frontier defaults (enforced when callers omit explicit model env overrides).
OPENAI_CHAT_FAST_MODEL = "gpt-5.5-instant"
OPENAI_REASONING_MODEL = "gpt-5.5-pro"


class OpenAIProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "openai"

    def _messages(self, message: str, system: str | None) -> list[dict[str, str]]:
        sys_text = (system or GLOBAL_CHAT_SYSTEM).strip()
        out: list[dict[str, str]] = [{"role": "system", "content": sys_text}]
        out.append({"role": "user", "content": message})
        return out

    async def send_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> ProviderSendResult:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        r = await cx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", **tenant_header(tenant_id)},
            json={"model": model, "messages": self._messages(message, system)},
        )
        r.raise_for_status()
        d = r.json()
        u = d.get("usage") or {}
        pi, po = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        return ProviderSendResult.from_token_counts(
            str(d["choices"][0]["message"]["content"]), pi, po
        )

    async def stream_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        async with cx.stream(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", **tenant_header(tenant_id)},
            json={
                "model": model,
                "messages": self._messages(message, system),
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield str(delta)
