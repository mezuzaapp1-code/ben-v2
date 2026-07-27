"""Anthropic messages API adapter."""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from services.chat_prompt import GLOBAL_CHAT_SYSTEM
from services.inference.usage_normalize import (
    merge_anthropic_stream_usage,
    normalize_anthropic_usage,
    usage_missing,
)
from services.providers.base_provider import (
    BaseProvider,
    ProviderSendResult,
    ProviderStreamEnd,
    tenant_header,
)
from services.providers.call_diagnostics import estimate_request_tokens, log_chat_provider_call

# May 2026 frontier defaults (enforced when callers omit explicit model env overrides).
ANTHROPIC_FLAGSHIP_MODEL = "claude-opus-4.8"
ANTHROPIC_FAST_MODEL = "claude-sonnet-4.6"


def _chat_max_tokens() -> int:
    raw = os.getenv("ANTHROPIC_CHAT_MAX_TOKENS", "1024").strip()
    try:
        return max(64, min(4096, int(raw)))
    except ValueError:
        return 1024


def anthropic_completion_truncated(
    payload: dict[str, Any],
    *,
    max_tokens: int,
    completion_tokens: int,
) -> bool:
    reason = str(payload.get("stop_reason") or "").strip().lower()
    if reason == "max_tokens":
        return True
    return max_tokens > 0 and completion_tokens >= max_tokens


class AnthropicProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _body(self, model: str, message: str, system: str | None, *, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": _chat_max_tokens(),
            "messages": [{"role": "user", "content": message}],
        }
        sys_text = (system or GLOBAL_CHAT_SYSTEM).strip()
        if sys_text:
            body["system"] = sys_text
        if stream:
            body["stream"] = True
        return body

    async def send_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> ProviderSendResult:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        max_tokens = _chat_max_tokens()
        req_est = estimate_request_tokens(message=message)
        t0 = time.perf_counter()
        ttfb_ms: int | None = None
        try:
            async with cx.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                    **tenant_header(tenant_id),
                },
                json=self._body(model, message, system, stream=False),
            ) as response:
                ttfb_ms = int((time.perf_counter() - t0) * 1000.0)
                response.raise_for_status()
                body = await response.aread()
            total_ms = int((time.perf_counter() - t0) * 1000.0)
            d = json.loads(body)
            txt = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
            usage = normalize_anthropic_usage(d.get("usage"))
            pi, po = usage.input_tokens, usage.output_tokens
            truncated = anthropic_completion_truncated(d, max_tokens=max_tokens, completion_tokens=po)
            stop = d.get("stop_reason")
            log_chat_provider_call(
                provider="anthropic",
                model=model,
                outcome="ok",
                duration_ms=total_ms,
                request_chars=len(message),
                request_tokens_est=req_est,
                prompt_tokens=pi,
                completion_tokens=po,
                ttfb_ms=ttfb_ms,
                max_tokens=max_tokens,
                truncation_detected=truncated,
                stop_reason=stop,
            )
            return ProviderSendResult.from_token_counts(
                txt,
                pi,
                po,
                completion_truncated=truncated,
                usage=usage,
                provider_request_id=str(d.get("id") or "") or None,
                finish_reason=str(stop) if stop else None,
            )
        except BaseException as e:
            total_ms = int((time.perf_counter() - t0) * 1000.0)
            from services.providers.provider_errors import sanitize_provider_error_message

            log_chat_provider_call(
                provider="anthropic",
                model=model,
                outcome="error",
                duration_ms=total_ms,
                request_chars=len(message),
                request_tokens_est=req_est,
                ttfb_ms=ttfb_ms,
                max_tokens=max_tokens,
                exc=e,
                error_class=type(e).__name__,
                error_message=sanitize_provider_error_message(e),
            )
            raise

    async def stream_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> AsyncIterator[str | ProviderStreamEnd]:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        usage = usage_missing()
        provider_request_id: str | None = None
        finish_reason: str | None = None
        async with cx.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                **tenant_header(tenant_id),
            },
            json=self._body(model, message, system, stream=True),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                et = event.get("type")
                if et == "message_start":
                    msg = event.get("message") or {}
                    if msg.get("id"):
                        provider_request_id = str(msg.get("id"))
                    if msg.get("usage"):
                        usage = normalize_anthropic_usage(msg.get("usage"))
                elif et == "content_block_delta":
                    text = (event.get("delta") or {}).get("text")
                    if text:
                        yield str(text)
                elif et == "message_delta":
                    delta = event.get("delta") or {}
                    if delta.get("stop_reason"):
                        finish_reason = str(delta.get("stop_reason"))
                    if event.get("usage"):
                        usage = merge_anthropic_stream_usage(usage, event.get("usage"))
                elif et == "message_stop":
                    break
        yield ProviderStreamEnd(
            usage=usage,
            provider_request_id=provider_request_id,
            finish_reason=finish_reason,
        )
