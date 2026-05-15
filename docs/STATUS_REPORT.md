# BEN TASK REPORT — Frontend Bearer Production Verification (final)

**Last updated:** 2026-05-15

## 1. Task Name

Frontend Clerk Bearer production verification after `VITE_CLERK_PUBLISHABLE_KEY` added to Vercel Production.

## 2. Branch

`main` (docs commit pending)

## 3. Goal

Redeploy Vercel production, verify Clerk key in bundle, sign-in UI, signed-in Bearer on `/chat` and `/council`, backend remains open (shadow mode), no token leakage. Update R-019 / R-020 only if verified.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `docs/STATUS_REPORT.md` | modified |
| `docs/RISK_REGISTER.md` | modified |

## 5. Code Changes

None (verification + docs).

## 6. Verification Executed

```bash
cd frontend
vercel env ls
vercel --prod --yes
python scripts/probe_vercel_clerk_bundle.py
python scripts/verify_auth_shadow_v1.py https://ben-v2-production.up.railway.app
python scripts/verify_frontend_bearer_e2e.py
python -c "… POST /chat, POST /council leak check …"
railway whoami
railway logs --lines 200
```

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| `VITE_CLERK_PUBLISHABLE_KEY` on Vercel | **PASS** | `vercel env ls`: Encrypted, Production |
| Production redeploy | **PASS** | `dpl_J2qXvcTGf7UTQVmJDJ4kWMAauPwc` → `ben-v2.vercel.app` |
| Frontend loads | **PASS** | HTTP 200 |
| `pk_*` in production bundle | **PASS** | `/assets/index-qRjddneO.js` — `publishable_key_in_bundle=PRESENT` |
| Sign-in UI on production | **PASS** | Playwright: `sign_in_button_visible=True` |
| `Authorization: Bearer` on `/chat` (signed-in) | **NOT VERIFIED** | No `CLERK_TEST_EMAIL` / `CLERK_TEST_PASSWORD`; flow skipped |
| `Authorization: Bearer` on `/council` (signed-in) | **NOT VERIFIED** | Same |
| Railway `auth_enforcement=false` | **PASS** | `/health` |
| Railway `auth_shadow_mode=true` | **PASS** | `/health` |
| POST `/council` no auth → 200 | **PASS** | Shape unchanged |
| POST `/council` invalid Bearer → 200 | **PASS** | Non-blocking |
| POST `/chat` → 200, shape OK | **PASS** | No JWT in body |
| JWT/token in API response bodies | **PASS** | No `eyJ` in chat/council JSON |
| `shadow_auth_check` prod logs | **NOT VERIFIED** | Railway CLI unauthorized |

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| Vercel env var exists (name only, value encrypted) | **VERIFIED** |
| Publishable key inlined in prod JS bundle | **VERIFIED** |
| Sign-in button visible on `ben-v2.vercel.app` | **VERIFIED** (Playwright) |
| Signed-in Bearer on `/chat` and `/council` | **NOT VERIFIED** |
| Backend shadow flags + signed-out/invalid-token API | **VERIFIED** |
| `auth_missing` / `auth_valid` in Railway logs | **NOT VERIFIED** |

## 8. Git Status

- Branch: `main`
- Pending: docs commit this session

## 9. Risks / Warnings

| ID | Status | Rationale |
|----|--------|-----------|
| **R-020** | **FIXED** | Risk was missing Vercel publishable key — **VERIFIED PRESENT** (env + bundle + sign-in UI). Signed-in Bearer header E2E still **NOT VERIFIED** (manual sign-in + DevTools or set `CLERK_TEST_EMAIL` / `CLERK_TEST_PASSWORD` and re-run `verify_frontend_bearer_e2e.py`). |
| **R-019** | **OPEN** | Railway logs not accessible (`railway login` required). |
| **R-013** | **PARTIAL** | Enforcement off; signed-in shadow `auth_valid` not log-verified. |

## 10. Manual Bearer verification (optional)

Sign in at `https://ben-v2.vercel.app` → DevTools → Network → POST to Railway `/chat` and `/council` → confirm `Authorization: Bearer` header (do not copy token into logs).

Or:

```bash
set CLERK_TEST_EMAIL=...
set CLERK_TEST_PASSWORD=...
python scripts/verify_frontend_bearer_e2e.py
```

## 11. Recommended Next Step

1. Manual or automated signed-in Network check for Bearer headers.
2. `railway login` → `railway logs --lines 200` → close R-019 if `shadow_auth_check` lines confirmed.
3. Phase 3 tenant binding before `ENFORCE_AUTH=true`.

## 12. Ready Status

**READY WITH WARNINGS** — Clerk key deployed; sign-in UI live; backend safe; signed-in Bearer headers and prod shadow logs **not fully verified**.

---

READY FOR CHATGPT REVIEW
