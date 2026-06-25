"""Frontier model allowlist, rate lookup, and provider API id resolution."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.providers.anthropic_provider import ANTHROPIC_FAST_MODEL, ANTHROPIC_FLAGSHIP_MODEL
from services.providers.gemini_provider import GEMINI_FAST_MODEL
from services.providers.openai_provider import OPENAI_CHAT_FAST_MODEL, OPENAI_REASONING_MODEL

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "shared" / "frontier_models.json"

_DEFAULT_RATES: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", OPENAI_CHAT_FAST_MODEL): (0.15e-6, 0.60e-6),
    ("openai", OPENAI_REASONING_MODEL): (2.5e-6, 10e-6),
    ("openai", "gpt-4o-mini"): (0.15e-6, 0.60e-6),
    ("openai", "gpt-4o"): (2.5e-6, 10e-6),
    ("anthropic", ANTHROPIC_FLAGSHIP_MODEL): (3e-6, 15e-6),
    ("anthropic", ANTHROPIC_FAST_MODEL): (1e-6, 5e-6),
    ("google", GEMINI_FAST_MODEL): (0.1e-6, 0.4e-6),
    ("google", "gemini-2.5-flash"): (0.1e-6, 0.4e-6),
}

_DEFAULT_API_ENV: dict[tuple[str, str], tuple[str, ...]] = {
    ("openai", OPENAI_CHAT_FAST_MODEL): ("OPENAI_CHAT_MODEL", "OPENAI_MODEL"),
    ("openai", OPENAI_REASONING_MODEL): ("OPENAI_REASONING_API_MODEL", "SYNTHESIS_MODEL", "OPENAI_MODEL"),
    ("anthropic", ANTHROPIC_FLAGSHIP_MODEL): ("ANTHROPIC_MODEL",),
    ("anthropic", ANTHROPIC_FAST_MODEL): ("ANTHROPIC_MODEL",),
    ("google", GEMINI_FAST_MODEL): ("GEMINI_MODEL", "GOOGLE_MODEL"),
}

# When env overrides are unset, dispatch to known-good provider API ids.
_DEFAULT_API_FALLBACK: dict[tuple[str, str], str] = {
    ("openai", OPENAI_CHAT_FAST_MODEL): "gpt-4o-mini",
    ("openai", OPENAI_REASONING_MODEL): "gpt-4o",
    ("anthropic", ANTHROPIC_FLAGSHIP_MODEL): "claude-sonnet-4-6",
    ("anthropic", ANTHROPIC_FAST_MODEL): "claude-sonnet-4-6",
    ("google", GEMINI_FAST_MODEL): "gemini-2.5-flash",
}


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, Any]:
    try:
        raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except OSError:
        return {}


@lru_cache(maxsize=1)
def allowed_models() -> frozenset[tuple[str, str]]:
    """All registered (provider, canonical_model) pairs that may traverse the gateway."""
    out: set[tuple[str, str]] = set(_DEFAULT_RATES.keys())
    data = _load_registry()
    providers = data.get("providers")
    if isinstance(providers, dict):
        gateway_map = {"openai": "openai", "anthropic": "anthropic", "google": "google"}
        for prov_key, spec in providers.items():
            gateway = gateway_map.get(str(prov_key).strip().lower())
            if not gateway or not isinstance(spec, dict):
                continue
            for field in ("chat_fast", "reasoning", "flagship", "fast"):
                model = str(spec.get(field) or "").strip()
                if model:
                    out.add((gateway, model))
            legacy = spec.get("legacy")
            if isinstance(legacy, list):
                for model in legacy:
                    mid = str(model or "").strip()
                    if mid:
                        out.add((gateway, mid))
    return frozenset(out)


def is_registered_model(provider: str, model: str) -> bool:
    prov = (provider or "").strip().lower()
    mid = (model or "").strip()
    if not prov or not mid:
        return False
    return (prov, mid) in allowed_models()


def assert_model_registered(provider: str, model: str) -> str:
    """Return canonical model id or raise ValueError before provider dispatch."""
    prov = (provider or "").strip().lower()
    mid = (model or "").strip()
    if not mid:
        raise ValueError(f"Missing model id for provider {prov or 'unknown'}")
    if not is_registered_model(prov, mid):
        registered = sorted(m for p, m in allowed_models() if p == prov)
        hint = ", ".join(registered) if registered else "none"
        raise ValueError(f"Model {mid!r} is not registered for {prov}. Allowed: {hint}")
    return mid


def token_rates(provider: str, model: str) -> tuple[float, float]:
    prov = (provider or "").strip().lower()
    mid = (model or "").strip()
    data = _load_registry()
    rates = data.get("token_rates_usd_per_token")
    if isinstance(rates, dict):
        key = f"{prov}:{mid}"
        raw = rates.get(key)
        if isinstance(raw, list) and len(raw) >= 2:
            try:
                return float(raw[0]), float(raw[1])
            except (TypeError, ValueError):
                pass
    return _DEFAULT_RATES.get((prov, mid), (0.5e-6, 1.5e-6))


def _env_override_keys(provider: str, model: str) -> tuple[str, ...]:
    prov = (provider or "").strip().lower()
    mid = (model or "").strip()
    data = _load_registry()
    overrides = data.get("api_env_overrides")
    if isinstance(overrides, dict):
        key = f"{prov}:{mid}"
        raw = overrides.get(key)
        if isinstance(raw, list):
            return tuple(str(x).strip() for x in raw if str(x).strip())
    return _DEFAULT_API_ENV.get((prov, mid), ())


def resolve_api_model(provider: str, canonical_model: str) -> str:
    """Map canonical BEN model id to provider API model id (env overrides optional)."""
    prov = (provider or "").strip().lower()
    canonical = assert_model_registered(prov, canonical_model)
    for env_key in _env_override_keys(prov, canonical):
        override = os.getenv(env_key, "").strip()
        if override:
            return override
    return _DEFAULT_API_FALLBACK.get((prov, canonical), canonical)


def registry_public_snapshot() -> dict[str, Any]:
    models = [{"provider": p, "model": m} for p, m in sorted(allowed_models())]
    return {
        "frontier_models_path": str(_REGISTRY_PATH),
        "registered_models": models,
        "openai_chat_fast": OPENAI_CHAT_FAST_MODEL,
        "openai_reasoning": OPENAI_REASONING_MODEL,
        "anthropic_flagship": ANTHROPIC_FLAGSHIP_MODEL,
        "anthropic_fast": ANTHROPIC_FAST_MODEL,
        "gemini_fast": GEMINI_FAST_MODEL,
    }
