"""Centralized timeout constants (seconds) aligned with docs/TIMING_GOVERNANCE.md.

Tiers:
  FAST       — /health, /ready, DB ping (hard 5s route budget)
  PRO        — single provider call, /chat gateway (hard 12s)
  DELIBERATE — council synthesis + persist envelope (hard 25s user-facing goal)
"""
from __future__ import annotations

import os

# --- Tier ceilings (governance) ---
FAST_HARD_TIMEOUT_S = 5.0
PRO_HARD_TIMEOUT_S = 12.0
DELIBERATE_HARD_TIMEOUT_S = 25.0


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --- FAST: health / readiness / DB ping ---
# Remote Postgres (e.g. Railway) from local dev often needs >2s on cold connect;
# pool warmup at startup keeps steady-state pings fast. Override via BEN_DB_PING_TIMEOUT_S.
DB_PING_TIMEOUT_S = _float_env("BEN_DB_PING_TIMEOUT_S", 4.5)
HEALTH_ROUTE_TIMEOUT_S = _float_env("BEN_HEALTH_ROUTE_TIMEOUT_S", FAST_HARD_TIMEOUT_S)

# --- PRO: provider HTTP (council experts, chat gateway) ---
HTTP_CLIENT_TIMEOUT_S = PRO_HARD_TIMEOUT_S
OPENAI_REQUEST_TIMEOUT_S = PRO_HARD_TIMEOUT_S
ANTHROPIC_REQUEST_TIMEOUT_S = PRO_HARD_TIMEOUT_S
EXPERT_CALL_TIMEOUT_S = PRO_HARD_TIMEOUT_S  # asyncio.wait_for per expert call

# Toolbar explicit provider_id on /chat (single attempt; Anthropic/Gemini often exceed 12s)
CHAT_EXPLICIT_PROVIDER_TIMEOUT_S = DELIBERATE_HARD_TIMEOUT_S

# --- DELIBERATE: synthesis + optional persistence ---
SYNTHESIS_TIMEOUT_S = 10.0  # governance matrix; leaves ~15s for parallel experts within 25s total
DB_OPERATION_TIMEOUT_S = _float_env("BEN_DB_OPERATION_TIMEOUT_S", 10.0)
COUNCIL_TOTAL_TIMEOUT_S = DELIBERATE_HARD_TIMEOUT_S  # outer envelope for POST /council (R-017)

# --- Council NDJSON stream (/council/stream) — aligned with frontend 300s idle ceiling ---
COUNCIL_STREAM_HTTP_CLIENT_TIMEOUT_S = 300.0  # httpx client envelope for sequential stream
COUNCIL_STREAM_EXPERT_CALL_TIMEOUT_S = 90.0  # per-expert asyncio.wait_for (sequential lane)
COUNCIL_STREAM_SYNTHESIS_TIMEOUT_S = _float_env("BEN_COUNCIL_STREAM_SYNTHESIS_TIMEOUT_S", 120.0)
