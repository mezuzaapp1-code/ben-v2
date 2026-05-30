"""Stable provider adapter interface for chat gateway calls."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

# content, total_tokens, prompt_tokens, completion_tokens
ProviderSendResult = tuple[str, int, int, int]


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
    ) -> ProviderSendResult:
        """Non-streaming completion; returns content and token counts."""

    async def stream_message(
        self,
        cx: httpx.AsyncClient,
        *,
        model: str,
        message: str,
        tenant_id: str,
    ) -> AsyncIterator[str]:
        """Yield response text. Default: single chunk (matches current non-SSE chat)."""
        content, _, _, _ = await self.send_message(
            cx, model=model, message=message, tenant_id=tenant_id
        )
        yield content
