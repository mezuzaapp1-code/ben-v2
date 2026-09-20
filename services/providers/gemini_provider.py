"""Google Gemini generateContent adapter."""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from services.chat_prompt import GLOBAL_CHAT_SYSTEM
from services.inference.usage_normalize import normalize_gemini_usage, usage_missing
from services.providers.base_provider import (
    BaseProvider,
    ProviderSendResult,
    ProviderStreamEnd,
    tenant_header,
)
from services.providers.vision_input import ProviderUserPart, gemini_user_parts
from services.ops.latency_path_audit import mark

# Current official Gemini Flash (exact dispatch; never remapped to another id).
# Catalog (ai.google.dev/gemini-api/docs/models, 2026-09-17): 3.8 Flash GA,
# 3.5 Flash still listed, 2.5 Flash still listed; 1.5 Flash is shut down.
GEMINI_FAST_MODEL = "gemini-3.8-flash"
GEMINI_CHAT_MODELS = ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash")
GEMINI_RETIRED_MODELS = frozenset({"gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.5-flash-8b"})


def assert_live_gemini_model(model: str) -> str:
    """Reject retired/unknown Gemini ids before any Google HTTP call."""
    mid = (model or "").strip()
    if not mid:
        raise ValueError("Missing Gemini model id")
    if mid in GEMINI_RETIRED_MODELS:
        raise ValueError(f"Model {mid!r} is retired and cannot be dispatched")
    if mid not in GEMINI_CHAT_MODELS:
        raise ValueError(f"Model {mid!r} is not a supported Gemini chat model")
    return mid


def resolve_gemini_default_model() -> str:
    """Gemini id used when chat omits model_override.

    GEMINI_MODEL / GOOGLE_MODEL are accepted only when they are a currently
    registered chat id. Retired or unknown values are ignored and do not
    remap a different selectable model. The env value is never logged.
    """
    raw = os.getenv("GEMINI_MODEL", "").strip() or os.getenv("GOOGLE_MODEL", "").strip()
    if not raw:
        return GEMINI_FAST_MODEL
    if raw in GEMINI_CHAT_MODELS and raw not in GEMINI_RETIRED_MODELS:
        return raw
    return GEMINI_FAST_MODEL


class GeminiProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "google"

    def _payload(
        self,
        message: str,
        system: str | None,
        *,
        user_content: list[ProviderUserPart] | None = None,
    ) -> dict[str, Any]:
        parts = gemini_user_parts(user_content) if user_content else [{"text": message}]
        payload: dict[str, Any] = {"contents": [{"parts": parts}]}
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
        user_content: list[ProviderUserPart] | None = None,
    ) -> ProviderSendResult:
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        model = assert_live_gemini_model(model)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = await cx.post(
            url,
            params={"key": api_key},
            headers=tenant_header(tenant_id),
            json=self._payload(message, system, user_content=user_content),
        )
        r.raise_for_status()
        d = r.json()
        parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        txt = "".join(p.get("text", "") for p in parts)
        usage = normalize_gemini_usage(d.get("usageMetadata"))
        finish = None
        cands = d.get("candidates") or []
        if cands and cands[0].get("finishReason"):
            finish = str(cands[0].get("finishReason"))
        return ProviderSendResult.from_token_counts(
            txt,
            usage.input_tokens,
            usage.output_tokens,
            usage=usage,
            finish_reason=finish,
        )

    async def stream_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
        user_content: list[ProviderUserPart] | None = None,
    ) -> AsyncIterator[str | ProviderStreamEnd]:
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        model = assert_live_gemini_model(model)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"
        usage = usage_missing()
        finish_reason: str | None = None
        async with cx.stream(
            "POST",
            url,
            params={"key": api_key, "alt": "sse"},
            headers=tenant_header(tenant_id),
            json=self._payload(message, system, user_content=user_content),
        ) as response:
            response.raise_for_status()
            mark("t5_http_headers", status=int(getattr(response, "status_code", 0) or 0))
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
                mark("t6_first_sse", has_candidates=bool(data.get("candidates")))
                if data.get("usageMetadata"):
                    usage = normalize_gemini_usage(data.get("usageMetadata"))
                candidates = data.get("candidates") or []
                if not candidates:
                    continue
                if candidates[0].get("finishReason"):
                    finish_reason = str(candidates[0].get("finishReason"))
                parts = ((candidates[0].get("content") or {}).get("parts") or [])
                for part in parts:
                    text = part.get("text")
                    if text:
                        if part.get("thought"):
                            mark("p3_reasoning", source="gemini_thought")
                        elif str(text).strip():
                            mark("p4_answer", source="gemini_text")
                        mark(
                            "t7_first_content",
                            source="gemini_text",
                            thought=bool(part.get("thought")),
                        )
                        yield str(text)
        mark("t9_provider_end")
        yield ProviderStreamEnd(
            usage=usage,
            finish_reason=finish_reason,
        )
