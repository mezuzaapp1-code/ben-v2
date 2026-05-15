# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Current Phase

**Auth shadow mode v1 live on production** (`d8a9407`). **ENFORCE_AUTH=false** — unauthenticated council/chat **still work**.

## Current Active Branch

`main` @ `d8a9407`

## Auth shadow deployment status

| Item | Status |
|------|--------|
| Deploy version | `d8a940716420591766cf745c0bf34620b447f43b` |
| `auth_enforcement` in prod `/health` | **false** — **VERIFIED** |
| `auth_shadow_mode` in prod `/health` | **true** — **VERIFIED** |
| Council without Authorization | **200** — **VERIFIED** |
| Council with invalid Bearer | **200** — **VERIFIED** |
| Railway `ENFORCE_AUTH` / `AUTH_SHADOW_MODE` vars | **NOT VERIFIED** (CLI unauthorized); health reflects expected flags |
| Prod `shadow_auth_check` logs | **NOT VERIFIED** (R-019 OPEN) |

## Enforcement status

**Disabled** — do not set `ENFORCE_AUTH=true` in Railway until frontend sends Clerk Bearer.

## Production smoke (session)

| Endpoint | HTTP | Notes |
|----------|------|-------|
| `GET /health` | 200 | `auth_enforcement=false`, `auth_shadow_mode=true` |
| `GET /ready` | 200 | `auth` block matches |
| `POST /council` (no auth) | 200 | 3 experts, synthesis present |
| `POST /council` (invalid Bearer) | 200 | shape unchanged |

## Open Risks

R-013 **PARTIAL**, R-014 **OPEN**, R-019 **OPEN**, R-015/R-016 unchanged.

## Recommended Next Step

1. **Frontend Bearer token wiring** on `/council` and `/chat`.
2. **Phase 3** tenant binding (`tenant_id` == JWT `org_id`).
3. `railway login` → confirm `shadow_auth_check` log lines (close R-019).

READY FOR CHATGPT REVIEW
