"""Tier 1 canonical model ids aligned with frontier registry."""
from __future__ import annotations

from services.providers.anthropic_provider import ANTHROPIC_FLAGSHIP_MODEL
from services.tier1_models import TIER1_GEMINI_MODEL, tier1_model_for


def test_tier1_claude_matches_flagship():
    assert tier1_model_for("claude") == ANTHROPIC_FLAGSHIP_MODEL


def test_tier1_gemini_is_registered_flash():
    assert tier1_model_for("gemini") == TIER1_GEMINI_MODEL
    assert TIER1_GEMINI_MODEL == "gemini-3.5-flash"


def test_tier1_gpt_is_registered_legacy():
    assert tier1_model_for("gpt") == "gpt-4o"
