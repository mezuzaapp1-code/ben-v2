# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Current Phase

**T-108 Phase 2 — auth shadow mode v1** on `feature/auth-shadow-mode-v1`. Observe auth first; **ENFORCE_AUTH=false** (default).

## Current Active Branch

`feature/auth-shadow-mode-v1`

## Current Active Task

Verify shadow mode locally; merge when ready. **Do not** set `ENFORCE_AUTH=true` in production yet.

## Auth shadow mode status

| Flag | Default | Production intent |
|------|---------|-------------------|
| `ENFORCE_AUTH` | `false` | Keep false until frontend sends Bearer |
| `AUTH_SHADOW_MODE` | `true` | Log `auth_missing` / `auth_valid` / `auth_invalid` on `/chat`, `/council` |

## Enforcement status

**Disabled** — unauthenticated requests still **HTTP 200** on council/chat when `ENFORCE_AUTH=false`.

## Recommended Next Step

1. Merge shadow mode → `main`; deploy with **ENFORCE_AUTH=false**.
2. Sample `ben.ops` logs for auth outcome distribution (R-019).
3. **Phase 3** tenant binding **or** frontend Clerk Bearer wiring before `ENFORCE_AUTH=true`.

## Open Risks

R-013 **PARTIAL**, R-014 **OPEN**, R-019 **OPEN** — see `docs/RISK_REGISTER.md`.

READY FOR CHATGPT REVIEW
