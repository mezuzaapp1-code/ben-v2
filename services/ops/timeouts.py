"""Centralized timeout constants (seconds). No unbounded provider or DB waits."""

# HTTP client (council parallel experts share one client)
HTTP_CLIENT_TIMEOUT_S = 120.0

# Per-provider request ceilings (httpx client default covers these; documented for ops)
OPENAI_REQUEST_TIMEOUT_S = 120.0
ANTHROPIC_REQUEST_TIMEOUT_S = 120.0

# BEN synthesis OpenAI call (asyncio.wait_for around single completion)
SYNTHESIS_TIMEOUT_S = 10.0

# Database
DB_PING_TIMEOUT_S = 2.0
DB_OPERATION_TIMEOUT_S = 5.0
