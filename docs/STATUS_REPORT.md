# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Current Phase

**Frontend Clerk Bearer wiring v1** on branch `feature/frontend-bearer-token-v1` (not merged). Production remains auth shadow `d8a9407`, **ENFORCE_AUTH=false**.

## Current Active Branch

`feature/frontend-bearer-token-v1` — pushes Bearer on `/chat` and `/council` when Clerk session exists; no backend enforcement change.

## Frontend Bearer status

| Item | Status |
|------|--------|
| `@clerk/clerk-react` + `ClerkProvider` | **IMPLEMENTED** (gated on `VITE_CLERK_PUBLISHABLE_KEY`) |
| `buildBenHeaders` → `Authorization: Bearer` | **IMPLEMENTED** for chat + council |
| Sign-in / sign-out UI | **IMPLEMENTED** when Clerk key set |
| Vite dev proxy `/chat`, `/council` | **IMPLEMENTED** |
| `npm run build` | **VERIFIED** (local) |
| Vercel `VITE_CLERK_PUBLISHABLE_KEY` | **NOT SET** (R-020 OPEN) |
| Prod enforce auth | **OFF** — unchanged |

## Auth shadow deployment status (production `main`)

| Item | Status |
|------|--------|
| Deploy version | `d8a940716420591766cf745c0bf34620b447f43b` |
| `auth_enforcement` in prod `/health` | **false** — **VERIFIED** |
| `auth_shadow_mode` in prod `/health` | **true** — **VERIFIED** |
| Prod `shadow_auth_check` logs | **NOT VERIFIED** (R-019 OPEN) |

## Open Risks

R-013 **PARTIAL**, R-014 **OPEN**, R-019 **OPEN**, R-020 **OPEN**, R-015/R-016 unchanged.

## Recommended Next Step

1. Merge `feature/frontend-bearer-token-v1` → set Vercel Clerk env → verify signed-in council shows `auth_valid` in shadow logs.
2. **Phase 3** tenant binding (`tenant_id` == JWT `org_id`).
3. Enable `ENFORCE_AUTH` only after frontend + tenant binding ready.

READY FOR CHATGPT REVIEW
