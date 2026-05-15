# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Current Phase

Runtime instrumentation v1 merged to `main` and production-verified (API). Log field visibility pending JSON formatter.

## Current Active Branch

`main` @ `2f016b8` (merge) includes `4d39da9` runtime instrumentation

## Current Active Task

Post-merge verification complete; next: JSON log formatter (R-008) or timing governance docs merge.

## Recently Completed Tasks

| Task | Outcome |
|------|---------|
| Runtime Instrumentation v1 | Merged `feature/runtime-instrumentation-v1` → `main`; prod smoke **PASS** |
| Hardening v1 | `/health`, `/ready`, `request_id`, graceful degradation live |
| Council synthesis | Prod **200**, synthesis + `cost_usd` |

## Blocked Tasks

None.

## Open Risks

R-002, R-003, R-008, R-009, **R-012 (PARTIAL)** — see `docs/RISK_REGISTER.md`.

## Production Status

| Item | Status |
|------|--------|
| URL | `https://ben-v2-production.up.railway.app` |
| `GET /health` | **200** — `healthy`, `database=ok`, `request_id` |
| `GET /ready` | **200** — `ready=true`, `migration_head=002_ko_synthesis_jsonb` |
| `POST /council` | **200** — 3 experts, synthesis present, `cost_usd` numeric, no raw provider errors |
| Timing logs in Railway UI | **NOT VERIFIED** (CLI unauthorized) |

## Runtime instrumentation (this release)

- `services/ops/timing.py` — `measure()`, `log_timing()` with `duration_ms`, `operation`, `outcome`
- Instrumented: `/health`, `/ready`, `/council`, OpenAI, Anthropic, synthesis, DB ping/migration/persist
- **No response shape changes**

## Deployment Readiness

**READY** for continued operation. **PARTIAL** observability until R-008 formatter deploys.

## Recommended Next Step

1. Add lightweight JSON log formatter for `ben.ops` (close R-008, complete R-012).
2. `railway login` → sample production logs for `GET /health completed`, `provider_openai`.
3. Merge `feature/timing-load-governance-v1` if not already on `main`.

READY FOR CHATGPT REVIEW
