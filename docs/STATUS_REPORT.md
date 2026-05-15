# BEN STATUS REPORT

**Last updated:** 2026-05-15

## Task: Frontend Bearer Token v1 — Merge & Production Verification

### 1. Task Name

Frontend Clerk Bearer merge to `main` + production verification (enforcement remains off).

### 2. Branch

`main` @ `89ddb64` (fast-forward merge from `feature/frontend-bearer-token-v1`)

### 3. Goal

Merge frontend Bearer wiring; confirm Railway auth shadow unchanged; smoke prod `/chat` and `/council`; document Vercel Clerk key status. **Do not** enable `ENFORCE_AUTH` or tenant binding.

### 4. Files Changed (merge commit)

| File | Change type |
|------|-------------|
| `frontend/src/api/benHeaders.js` | added |
| `frontend/src/auth/BenAuthContext.jsx` | added |
| `frontend/src/hooks/useBenAuth.js` | added |
| `frontend/src/config.js` | added |
| `frontend/.env.example` | added |
| `frontend/src/App.jsx`, `main.jsx`, `App.css`, `vite.config.js` | modified |
| `frontend/package.json`, `package-lock.json` | modified |
| `frontend/README.md` | modified |

### 5. Code Changes

Frontend sends `Authorization: Bearer` on `/chat` and `/council` when Clerk session exists and `VITE_CLERK_PUBLISHABLE_KEY` is set at build time. Without the key, app runs unsigned (no sign-in UI). Backend unchanged; `ENFORCE_AUTH=false`.

### 6. Verification Executed

```bash
git branch --show-current          # feature/frontend-bearer-token-v1 → main
git status -sb                     # clean
git rev-parse 89ddb64 origin/main  # 89ddb645cf7bea3e38d83529a833517c00ac61bb
git checkout main && git pull && git merge feature/frontend-bearer-token-v1
git push -v origin HEAD            # 79a73d3..89ddb64  main -> main
cd frontend && npm run build       # PASS
python scripts/verify_auth_shadow_v1.py https://ben-v2-production.up.railway.app
python -c "… POST /chat, POST /council signed-out smoke …"
# httpx probe: ben-v2.vercel.app HTML + /assets/*.js for pk_* (presence only)
vercel env ls                        # NOT VERIFIED — CLI prompted login
railway logs                         # NOT VERIFIED — Unauthorized
```

Signed-in browser / Network tab Bearer header: **NOT EXECUTED** (no Clerk publishable key in deployed bundle).

### 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| Branch + clean tree + `89ddb64` on origin | **PASS** | Pre-merge |
| No secrets in repo / no token logging in frontend | **PASS** | Grep; only header builder uses token |
| `npm run build` | **PASS** | 98 modules, ~2.8s |
| R-013 PARTIAL / R-014 OPEN | **PASS** | Register unchanged status |
| Merge to `main` + push | **PASS** | `origin/main` = `89ddb64` |
| Railway `auth_enforcement=false` | **PASS** | `/health` + `/ready` |
| Railway `auth_shadow_mode=true` | **PASS** | `/health` + `/ready` |
| `ENFORCE_AUTH` not enabled | **PASS** | Verified via prod `/health` |
| POST `/council` no auth → 200, shape OK | **PASS** | `verify_auth_shadow_v1.py` |
| POST `/council` bad Bearer → 200 | **PASS** | Shape unchanged |
| POST `/chat` signed-out → 200 | **PASS** | Keys: `cost_usd`, `model_used`, `response`, `thread_id` |
| Vercel `VITE_CLERK_PUBLISHABLE_KEY` (dashboard) | **NOT VERIFIED** | CLI login required |
| Vercel bundle publishable key | **MISSING** | Probe: `https://ben-v2.vercel.app` JS has Clerk lib hints, no `pk_*` inlined |
| Vercel frontend loads | **PASS** | HTTP 200 |
| Signed-in Bearer header (browser) | **NOT VERIFIED** | Blocked: key missing in bundle |
| `shadow_auth_check` prod logs | **NOT VERIFIED** | Railway CLI unauthorized (R-019 OPEN) |

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| `origin/main` contains `89ddb64` | **VERIFIED** |
| `auth_enforcement=false`, `auth_shadow_mode=true` on prod | **VERIFIED** |
| Signed-out `/chat` and `/council` HTTP 200 + council shape | **VERIFIED** |
| Vercel Clerk publishable key absent from deployed JS | **VERIFIED** (bundle probe; redeploy after env set required) |
| Vercel dashboard env var set/unset | **NOT VERIFIED** |
| Signed-in `auth_valid` shadow logs | **NOT VERIFIED** |

### 8. Git Status

- **Branch:** `main` @ `89ddb64`
- **Push:** `origin/main` updated (`79a73d3..89ddb64`)
- **Working tree:** clean after merge (docs commit pending this report)

### 9. Risks / Warnings

- **R-013** PARTIAL — Bearer wiring merged; enforcement still off; unsigned API still open.
- **R-014** OPEN — `tenant_id` not JWT-bound.
- **R-019** OPEN — prod `shadow_auth_check` logs not sampled.
- **R-020** OPEN — `VITE_CLERK_PUBLISHABLE_KEY` **MISSING** in live Vercel bundle; set env + redeploy before signed-in E2E.
- Vercel may still be serving a pre-merge build until CI redeploy completes; re-probe bundle after deploy.

### 10. Recommended Next Step

1. Set `VITE_CLERK_PUBLISHABLE_KEY` on Vercel (Production) and redeploy.
2. **Phase 3 tenant binding** — validate `tenant_id` against JWT `org_id` before enabling enforcement.
3. `railway login` → sample `shadow_auth_check` for `auth_missing` / `auth_valid` (close R-019).

### 11. Ready Status

**READY WITH WARNINGS** — merge complete; backend safe; signed-in frontend/auth-log verification blocked on Vercel Clerk env + Railway logs.

---

## Current Phase

**Frontend Bearer v1 merged to `main`.** Production Railway auth shadow unchanged. Vercel Clerk key **MISSING** in deployed bundle probe.

## Current Active Branch

`main` @ `89ddb64`

## Vercel Clerk key

| Source | Status |
|--------|--------|
| Dashboard (`VITE_CLERK_PUBLISHABLE_KEY`) | **NOT VERIFIED** (CLI login required) |
| Deployed bundle probe | **MISSING** |

## Backend enforcement (production)

| Flag | Status |
|------|--------|
| `auth_enforcement` | **false** — **VERIFIED** |
| `auth_shadow_mode` | **true** — **VERIFIED** |
| `ENFORCE_AUTH` enabled | **No** |

READY FOR CHATGPT REVIEW
