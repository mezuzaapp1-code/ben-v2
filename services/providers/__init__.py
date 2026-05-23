"""Gateway provider adapters (pluggable by gateway provider name)."""
from __future__ import annotations

from services.providers.anthropic_provider import AnthropicProvider
from services.providers.base_provider import BaseProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.openai_provider import OpenAIProvider

_GATEWAY_PROVIDERS: dict[str, BaseProvider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "google": GeminiProvider(),
}

_PROVIDER_API_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
}


def get_gateway_provider(name: str) -> BaseProvider:
    try:
        return _GATEWAY_PROVIDERS[name]
    except KeyError as exc:
        raise KeyError(f"unknown gateway provider: {name}") from exc


def gateway_provider_api_key_env(name: str) -> str:
    return _PROVIDER_API_KEYS[name]
