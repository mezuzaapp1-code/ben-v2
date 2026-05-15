# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Current Phase

**Timeout budget alignment v1** on `feature/timeout-budget-alignment-v1` — runtime constants aligned to `TIMING_GOVERNANCE.md` tiers. Security baseline docs on `main`. Auth **not** enforced.

## Current Active Branch

`feature/timeout-budget-alignment-v1`

## Current Active Task

Verify timeout alignment locally; merge after review. **Next after merge:** T-108 Phase 1 (secrets hygiene) before auth enforcement.

## Recently Completed Tasks

| Task | Outcome |
|------|---------|
| Timeout alignment v1 (branch) | `timeouts.py` tiers; PRO 12s providers; FAST 5s health; parallel experts |
| Security Baseline v1 docs | On `main` |
| JSON logging v1 | On `main` (`82739c2`) |

## Blocked Tasks

Full DELIBERATE 25s envelope enforcement — **PARTIAL** (R-017: theoretical 27s worst case).

## Open Risks

R-002, R-003, **R-010 (PARTIAL)**, R-011, **R-012 (PARTIAL)**, R-013–R-016, **R-017** — see `docs/RISK_REGISTER.md`.

## Timeout alignment state

| Tier | Constant | Value |
|------|----------|-------|
| FAST | `HEALTH_ROUTE_TIMEOUT_S` | 5s |
| FAST | `DB_PING_TIMEOUT_S` | 2s |
| PRO | `HTTP_CLIENT_TIMEOUT_S` / `EXPERT_CALL_TIMEOUT_S` | 12s |
| DELIBERATE | `SYNTHESIS_TIMEOUT_S` | 10s |
| DELIBERATE | `DB_OPERATION_TIMEOUT_S` | 5s (optional persist) |

**Remaining gaps:** no request-level DELIBERATE hard cap; `/chat` uses PRO 12s only; load isolation (R-010) and rate limits (R-015) not implemented.

## Production Status

Unchanged until branch merges and deploys.

## Recommended Next Step

1. Review and merge `feature/timeout-budget-alignment-v1` → `main`; prod smoke.
2. **T-108 Phase 1** — secrets/repo hygiene.
3. Optional: outer 25s council envelope (close R-017).

READY FOR CHATGPT REVIEW
