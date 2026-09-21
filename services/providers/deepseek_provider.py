"""DeepSeek policy on BEN's shared Chat Completions transport.

Normal chat uses non-thinking mode. Tools and conversation/context ownership
stay in BEN; provider reasoning is never emitted as assistant content.
"""
from __future__ import annotations

from dataclasses import replace

from services.inference.contracts import InferenceUsage
from services.inference.usage_normalize import normalize_openai_usage
from services.providers.openai_provider import OpenAIProvider

DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"
DEEPSEEK_MODELS = frozenset({DEEPSEEK_DEFAULT_MODEL, "deepseek-v4-pro"})


def deepseek_chat_payload(*, model: str, messages: list[dict], stream: bool = False) -> dict:
    if model not in DEEPSEEK_MODELS:
        raise ValueError("Unregistered DeepSeek model")
    if model == "deepseek-v4-pro" and any(
        isinstance(m.get("content"), list)
        and any(p.get("type") != "text" for p in m["content"])
        for m in messages
    ):
        raise ValueError("DeepSeek V4 Pro does not support image input")
    payload = {"model": model, "messages": messages, "thinking": {"type": "disabled"}}
    if stream:
        payload.update(stream=True, stream_options={"include_usage": True})
    return payload


def normalize_deepseek_usage(raw: dict | None) -> InferenceUsage:
    if isinstance(raw, dict) and "prompt_cache_hit_tokens" in raw:
        raw = {**raw, "prompt_tokens_details": {"cached_tokens": raw["prompt_cache_hit_tokens"]}}
    try:
        usage = normalize_openai_usage(raw)
    except (TypeError, ValueError):
        raise ValueError("DeepSeek returned invalid usage") from None
    # BEN prices output and reasoning separately; DeepSeek completion_tokens
    # already includes reasoning. Do not bill those tokens twice.
    return replace(usage, output_tokens=max(0, usage.output_tokens - usage.reasoning_tokens))


class DeepSeekProvider(OpenAIProvider):
    api_key_env = "DEEPSEEK_API_KEY"
    completions_url = "https://api.deepseek.com/chat/completions"
    _payload = staticmethod(deepseek_chat_payload)
    _normalize_usage = staticmethod(normalize_deepseek_usage)
    strict_stream = True

    @property
    def provider_name(self) -> str:
        return "deepseek"

    def _check_response(self, data: dict) -> None:
        # Never propagate arbitrary remote error text (it can contain secrets).
        if not isinstance(data, dict):
            raise ValueError("DeepSeek returned an invalid response")
        if data.get("error"):
            raise ValueError("DeepSeek provider returned an error")
        for choice in data.get("choices") or []:
            if choice.get("finish_reason") in ("insufficient_system_resource", "aborted", "tool_calls"):
                raise ValueError("DeepSeek response could not complete")

    async def send_message(self, cx, **kwargs):
        result = await super().send_message(cx, **kwargs)
        if not result.content:
            raise ValueError("DeepSeek returned no answer content")
        usage = result.usage
        return replace(
            result,
            total_tokens=usage.normalized_total(),
            completion_tokens=usage.output_tokens + usage.reasoning_tokens,
            completion_truncated=result.finish_reason == "length",
        )

    def _check_stream_end(self, finish_reason: str | None) -> None:
        if not finish_reason:
            raise ValueError("DeepSeek stream ended without a completion status")
