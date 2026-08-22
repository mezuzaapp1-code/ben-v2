"""Canonical Tier 1 flagship model ids — must match shared/frontier_models.json allowlist."""
from __future__ import annotations

from services.providers.anthropic_provider import ANTHROPIC_FLAGSHIP_MODEL
from services.providers.xai_provider import XAI_FLAGSHIP_MODEL

# gpt-4o is registered legacy; resolves to a known-good OpenAI API id via model_registry.
TIER1_GPT_MODEL = "gpt-4o"
TIER1_CLAUDE_MODEL = ANTHROPIC_FLAGSHIP_MODEL
TIER1_GEMINI_MODEL = "gemini-3.5-flash"
TIER1_GROK_MODEL = XAI_FLAGSHIP_MODEL

TIER1_MODEL_BY_PROVIDER: dict[str, str] = {
    "gpt": TIER1_GPT_MODEL,
    "claude": TIER1_CLAUDE_MODEL,
    "gemini": TIER1_GEMINI_MODEL,
    "grok": TIER1_GROK_MODEL,
}


def tier1_model_for(provider_id: str) -> str:
    return TIER1_MODEL_BY_PROVIDER.get((provider_id or "").strip().lower(), "")
