"""xAI Chat Completions adapter (OpenAI-compatible HTTP, isolated key and URL)."""
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

XAI_CHAT_COMPLETIONS_URL = "https://api.x.ai/v1/chat/completions"
XAI_FLAGSHIP_MODEL = "grok-4.6"
XAI_FAST_MODEL = "grok-4.3"

# Chat Completions search_parameters.mode defaults to "on". V1 text chat must not
# independently invoke Web/X search; BEN decides external capabilities.
_SEARCH_OFF = {"mode": "off"}


class XAIProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "xai"

    def _messages(self, message: str, system: str | None) -> list[dict[str, str]]:
        sys_text = (system or GLOBAL_CHAT_SYSTEM).strip()
        return [{"role": "system", "content": sys_text}, {"role": "user", "content": message}]

    def _json_body(self, model: str, message: str, system: str | None, *, stream: bool) -> dict:
        body: dict = {
            "model": model,
            "messages": self._messages(message, system),
            "search_parameters": dict(_SEARCH_OFF),
        }
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    def _headers(self, tenant_id: str) -> dict[str, str]:
        api_key = os.getenv("XAI_API_KEY", "").strip()
        return {"Authorization": f"Bearer {api_key}", **tenant_header(tenant_id)}

    async def send_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> ProviderSendResult:
        r = await cx.post(
            XAI_CHAT_COMPLETIONS_URL,
            headers=self._headers(tenant_id),
            json=self._json_body(model, message, system, stream=False),
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
        usage = usage_missing()
        provider_request_id: str | None = None
        finish_reason: str | None = None
        async with cx.stream(
            "POST",
            XAI_CHAT_COMPLETIONS_URL,
            headers=self._headers(tenant_id),
            json=self._json_body(model, message, system, stream=True),
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
