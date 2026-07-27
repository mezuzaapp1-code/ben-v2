"""OpenAI chat completions adapter."""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx

from services.chat_prompt import GLOBAL_CHAT_SYSTEM
from services.inference.usage_normalize import normalize_openai_usage, usage_missing
from services.providers.base_provider import (
    BaseProvider,
    ProviderSendResult,
    ProviderStreamEnd,
    tenant_header,
)

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
        usage = normalize_openai_usage(d.get("usage"))
        choice = (d.get("choices") or [{}])[0]
        content = str((choice.get("message") or {}).get("content") or "")
        finish = choice.get("finish_reason")
        return ProviderSendResult.from_token_counts(
            content,
            usage.input_tokens,
            usage.output_tokens,
            usage=usage,
            provider_request_id=str(d.get("id") or "") or None,
            finish_reason=str(finish) if finish else None,
        )

    async def stream_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> AsyncIterator[str | ProviderStreamEnd]:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        usage = usage_missing()
        provider_request_id: str | None = None
        finish_reason: str | None = None
        async with cx.stream(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", **tenant_header(tenant_id)},
            json={
                "model": model,
                "messages": self._messages(message, system),
                "stream": True,
                "stream_options": {"include_usage": True},
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
                if data.get("id") and not provider_request_id:
                    provider_request_id = str(data.get("id"))
                if data.get("usage"):
                    usage = normalize_openai_usage(data.get("usage"))
                choices = data.get("choices") or []
                if not choices:
                    continue
                choice0 = choices[0]
                if choice0.get("finish_reason"):
                    finish_reason = str(choice0.get("finish_reason"))
                delta = (choice0.get("delta") or {}).get("content")
                if delta:
                    yield str(delta)
        yield ProviderStreamEnd(
            usage=usage,
            provider_request_id=provider_request_id,
            finish_reason=finish_reason,
        )
