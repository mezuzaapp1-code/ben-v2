"""Speaking-provider catalog (leaf module — no gateway or message_format imports)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "shared" / "speaking_providers.json"


@dataclass(frozen=True)
class SpeakingProviderSpec:
    id: str
    label: str
    short_label: str
    gateway: str
    api_key_env: str
    accent: str


def _load_specs() -> tuple[SpeakingProviderSpec, ...]:
    raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    providers = raw.get("providers") if isinstance(raw, dict) else raw
    if not isinstance(providers, list):
        raise ValueError("speaking_providers.json must contain a providers array")
    out: list[SpeakingProviderSpec] = []
    for item in providers:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip().lower()
        if not pid:
            continue
        out.append(
            SpeakingProviderSpec(
                id=pid,
                label=str(item.get("label") or pid),
                short_label=str(item.get("short_label") or item.get("label") or pid),
                gateway=str(item.get("gateway") or "").strip().lower(),
                api_key_env=str(item.get("api_key_env") or ""),
                accent=str(item.get("accent") or ""),
            )
        )
    if not out:
        raise ValueError("speaking_providers.json has no valid providers")
    return tuple(out)


@lru_cache(maxsize=1)
def all_speaking_providers() -> tuple[SpeakingProviderSpec, ...]:
    return _load_specs()


@lru_cache(maxsize=1)
def _by_id() -> dict[str, SpeakingProviderSpec]:
    return {p.id: p for p in all_speaking_providers()}


@lru_cache(maxsize=1)
def _gateway_to_id() -> dict[str, str]:
    return {p.gateway: p.id for p in all_speaking_providers()}


def all_provider_ids() -> frozenset[str]:
    return frozenset(_by_id().keys())


def normalize_speaking_provider_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    pid = str(raw).strip().lower()
    if not pid:
        return None
    if pid not in _by_id():
        raise ValueError(f"provider_id must be one of: {', '.join(sorted(_by_id()))}")
    return pid


def provider_label(provider_id: str) -> str:
    spec = _by_id().get((provider_id or "").strip().lower())
    return spec.label if spec else ""


def gateway_for_provider_id(provider_id: str) -> str:
    spec = _by_id().get((provider_id or "").strip().lower())
    if spec is None:
        raise KeyError(f"unknown speaking provider_id: {provider_id}")
    return spec.gateway


def gateway_to_provider_id(gateway: str) -> str:
    return _gateway_to_id().get((gateway or "").strip().lower(), "")


def provider_accent(provider_id: str) -> str:
    spec = _by_id().get((provider_id or "").strip().lower())
    return spec.accent if spec else ""


def provider_spec(provider_id: str) -> SpeakingProviderSpec | None:
    return _by_id().get((provider_id or "").strip().lower())


def registry_public_snapshot() -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "label": p.label,
            "short_label": p.short_label,
            "gateway": p.gateway,
            "accent": p.accent,
        }
        for p in all_speaking_providers()
    ]
