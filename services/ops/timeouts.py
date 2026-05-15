"""Centralized timeout constants (seconds) aligned with docs/TIMING_GOVERNANCE.md.

Tiers:
  FAST       — /health, /ready, DB ping (hard 5s route budget)
  PRO        — single provider call, /chat gateway (hard 12s)
  DELIBERATE — council synthesis + persist envelope (hard 25s user-facing goal)
"""

# --- Tier ceilings (governance) ---
FAST_HARD_TIMEOUT_S = 5.0
PRO_HARD_TIMEOUT_S = 12.0
DELIBERATE_HARD_TIMEOUT_S = 25.0

# --- FAST: health / readiness / DB ping ---
DB_PING_TIMEOUT_S = 2.0  # stays under FAST 5s; leaves budget for env checks + serialization
HEALTH_ROUTE_TIMEOUT_S = FAST_HARD_TIMEOUT_S  # outer cap for /health and /ready handlers

# --- PRO: provider HTTP (council experts, chat gateway) ---
HTTP_CLIENT_TIMEOUT_S = PRO_HARD_TIMEOUT_S
OPENAI_REQUEST_TIMEOUT_S = PRO_HARD_TIMEOUT_S
ANTHROPIC_REQUEST_TIMEOUT_S = PRO_HARD_TIMEOUT_S
EXPERT_CALL_TIMEOUT_S = PRO_HARD_TIMEOUT_S  # asyncio.wait_for per expert call

# --- DELIBERATE: synthesis + optional persistence ---
SYNTHESIS_TIMEOUT_S = 10.0  # governance matrix; leaves ~15s for parallel experts within 25s total
DB_OPERATION_TIMEOUT_S = 5.0  # optional KO persist; degrades without failing council HTTP 200
COUNCIL_TOTAL_TIMEOUT_S = DELIBERATE_HARD_TIMEOUT_S  # outer envelope for POST /council (R-017)
