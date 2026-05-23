"""Stable provider adapter interface for chat gateway calls."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ProviderSendResult:
    content: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    completion_truncated: bool = False

    @classmethod
    def from_token_counts(
        cls,
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        completion_truncated: bool = False,
    ) -> ProviderSendResult:
        pi, po = int(prompt_tokens), int(completion_tokens)
        return cls(
            content=content,
            total_tokens=pi + po,
            prompt_tokens=pi,
            completion_tokens=po,
            completion_truncated=completion_truncated,
        )


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
        result = await self.send_message(
            cx, model=model, message=message, tenant_id=tenant_id
        )
        yield result.content
