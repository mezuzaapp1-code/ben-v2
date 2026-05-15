# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Current Phase

**R-017 outer council 25s cap live in production** (`0213d6d`). Prod smoke **PASS** (council **6.70s**). Auth **not** enforced.

## Current Active Branch

`main` @ `0213d6d`

## Current Active Task

**T-108 Phase 2** — auth shadow mode (`ENFORCE_AUTH=false` default, Clerk wiring).

## Recently Completed Tasks

| Task | Outcome |
|------|---------|
| R-017 merge + prod verify | Outer `wait_for` 25s; prod council 6.70s; shape unchanged |
| Secrets hygiene v1 | On `main` |
| Timeout alignment v1 | On `main` |

## R-017 production verification

| Check | Result |
|-------|--------|
| Deploy version `0213d6d` | **VERIFIED** |
| Council wall-clock ≤ 26s | **VERIFIED** (6.70s) |
| HTTP 200, 3 experts, synthesis | **VERIFIED** |
| Forced outer timeout (local only) | **VERIFIED** |

## Open Risks

R-002, R-010 (**PARTIAL**), R-011, R-012 (**PARTIAL**), R-013–R-016, R-018 — see `docs/RISK_REGISTER.md`.

## Production status

| Endpoint | Status | Wall-clock (session) |
|----------|--------|----------------------|
| `GET /health` | 200 healthy | 0.74s |
| `GET /ready` | 200 ready | 0.25s |
| `POST /council` | 200, synthesis present | **6.70s** |

## Recommended Next Step

**T-108 Phase 2** — wire Clerk `get_current_user` on `/chat` and `/council` with `ENFORCE_AUTH=false` until frontend sends Bearer.

READY FOR CHATGPT REVIEW
