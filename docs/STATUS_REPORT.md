# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Current Phase

**Security Baseline v1 documentation merged to `main`** (`1114462`). No runtime auth enforcement. Timing governance + JSON logging on `main`. Production APIs remain **unauthenticated** (by design until T-108 Phase 2).

## Current Active Branch

`main` @ `1114462` (+ docs follow-up commit pending)

## Current Active Task

**Next implementation layer:** timeout budget alignment (`timeouts.py` vs `TIMING_GOVERNANCE.md`). **Security:** T-108 Phase 1–2 when ready (secrets hygiene, then Clerk wiring behind `ENFORCE_AUTH`).

## Recently Completed Tasks

| Task | Outcome |
|------|---------|
| Security Baseline v1 docs | Merged `feature/security-baseline-v1` → `main`; R-013–R-016 registered |
| Timing governance docs | On `main` (`ac89049`) |
| JSON logging v1 | On `main` (`82739c2`); prod API smoke **PASS** |

## Blocked Tasks

Auth enforcement (T-108 Phase 2) — intentionally deferred; requires Clerk prod config + frontend Bearer + `ENFORCE_AUTH` flag.

## Open Risks

R-002, R-003, R-010, R-011, **R-012 (PARTIAL)**, **R-013 (HIGH, OPEN)**, **R-014 (HIGH, OPEN)**, **R-015 (OPEN)**, **R-016 (OPEN)** — see `docs/RISK_REGISTER.md`.

## Production Status

| Item | Status |
|------|--------|
| Deploy | Unchanged by docs-only merge (last API verify `82739c2`) |
| Auth on council/chat | **Not enforced** (R-013) |
| JSON `ben.ops` in Railway | **NOT VERIFIED** |

## Security artifacts (on main)

| Doc | Purpose |
|-----|---------|
| `docs/SECURITY_BASELINE.md` | Auth, tenant, routes, hardening, T-108 phases |
| `docs/SECRETS_GOVERNANCE.md` | Env classes, rotation, logging redaction |
| `docs/TASK_QUEUE.md` | T-108 Phases 1–5 defined |

## Deployment Readiness

**READY** — docs-only merge complete; no deploy required. **NOT READY** for auth hardening until T-108 Phase 2+.

## Recommended Next Step

1. **Timeout budget alignment** — `services/ops/timeouts.py` vs `TIMING_GOVERNANCE.md`.
2. **T-108 Phase 1** — secrets/repo hygiene (`.gitignore` test JSON).
3. **T-108 Phase 2** — wire Clerk with `ENFORCE_AUTH=false` default until frontend ready.

READY FOR CHATGPT REVIEW
