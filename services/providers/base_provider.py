"""Stable provider adapter interface for chat gateway calls."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from services.inference.contracts import InferenceUsage
from services.inference.usage_normalize import usage_missing


@dataclass(frozen=True)
class ProviderSendResult:
    content: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    completion_truncated: bool = False
    usage: InferenceUsage | None = None
    provider_request_id: str | None = None
    finish_reason: str | None = None

    @classmethod
    def from_token_counts(
        cls,
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        completion_truncated: bool = False,
        usage: InferenceUsage | None = None,
        provider_request_id: str | None = None,
        finish_reason: str | None = None,
    ) -> ProviderSendResult:
        pi, po = int(prompt_tokens), int(completion_tokens)
        resolved = usage
        if resolved is None and (pi or po):
            resolved = InferenceUsage(
                input_tokens=pi,
                output_tokens=po,
                total_tokens=pi + po,
                usage_status="exact",
            )
        if resolved is None:
            resolved = usage_missing()
        return cls(
            content=content,
            total_tokens=pi + po if (pi or po) else resolved.normalized_total(),
            prompt_tokens=pi or resolved.input_tokens,
            completion_tokens=po or resolved.output_tokens,
            completion_truncated=completion_truncated,
            usage=resolved,
            provider_request_id=provider_request_id,
            finish_reason=finish_reason,
        )


@dataclass(frozen=True)
class ProviderStreamEnd:
    """Terminal stream event carrying normalized usage (may be missing)."""

    usage: InferenceUsage
    provider_request_id: str | None = None
    finish_reason: str | None = None


def tenant_header(tenant_id: str) -> dict[str, str]:
    return {"X-BEN-Tenant": tenant_id}


class BaseProvider(ABC):
    """Thin HTTP adapter — no routing, orchestration, or business rules."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Gateway provider key (openai, anthropic, google)."""

    @abstractmethod
    async def send_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> ProviderSendResult:
        """Non-streaming completion; returns content and token counts."""

    async def stream_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
        system: str | None = None,
    ) -> AsyncIterator[str | ProviderStreamEnd]:
        """Yield response text chunks; final item may be ProviderStreamEnd with usage."""
        result = await self.send_message(
            cx, model=model, message=message, tenant_id=tenant_id, system=system
        )
        if result.content:
            yield result.content
        yield ProviderStreamEnd(
            usage=result.usage or usage_missing(),
            provider_request_id=result.provider_request_id,
            finish_reason=result.finish_reason,
        )
