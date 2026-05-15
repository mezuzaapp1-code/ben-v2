# BEN STATUS REPORT — Auth Bearer Production Verification (continued)

**Last updated:** 2026-05-15

## 1. Task Name

Frontend Clerk Bearer production verification (Vercel CLI authenticated).

## 2. Branch

`main` @ `887c89e` (code merge `89ddb64` + prior docs)

## 3. Goal

Verify `VITE_CLERK_PUBLISHABLE_KEY` on Vercel, redeploy frontend, confirm signed-in Bearer E2E and auth shadow logs. **Do not** enable `ENFORCE_AUTH` or tenant binding.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `docs/STATUS_REPORT.md` | modified |
| `docs/RISK_REGISTER.md` | modified |

## 5. Code Changes

None (verification + docs only).

## 6. Verification Executed

```bash
cd frontend
vercel whoami                                    # info-58606460
vercel link --yes                                # ben-ai-s-projects/ben-v2
vercel env ls                                    # no variables
vercel --prod --yes                              # production redeploy → ben-v2.vercel.app
python scripts/probe_vercel_clerk_bundle.py      # session probe (deleted after)
python scripts/verify_auth_shadow_v1.py https://ben-v2-production.up.railway.app
railway whoami                                   # Unauthorized
railway logs --lines 200                         # NOT EXECUTED (unauthorized)
```

Signed-in browser E2E (Clerk sign-in, Network tab Bearer on `/chat`, `/council`): **NOT EXECUTED** — blocked by missing Vercel env var.

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| Vercel CLI authenticated | **PASS** | `vercel whoami` |
| `VITE_CLERK_PUBLISHABLE_KEY` on Vercel | **MISSING** | `vercel env ls`: zero env vars for project |
| Production redeploy | **PASS** | `dpl_uDnsRqXuAgUpudy4YA2EuHde2Roq` → `https://ben-v2.vercel.app` |
| Frontend loads | **PASS** | HTTP 200 |
| Bundle contains `pk_*` | **MISSING** | `/assets/index-DsZTVMqA.js` — no publishable key inlined |
| Clerk sign-in UI / signed-in flow | **NOT VERIFIED** | No key at build time → no Sign in UI |
| `Authorization: Bearer` on `/chat`, `/council` | **NOT VERIFIED** | Requires Clerk session + env var |
| Railway `auth_enforcement=false` | **PASS** | `/health` |
| Railway `auth_shadow_mode=true` | **PASS** | `/health` |
| POST `/council` no auth → 200 | **PASS** | Shape unchanged |
| POST `/council` invalid Bearer → 200 | **PASS** | Non-blocking (shadow mode) |
| `shadow_auth_check` prod logs | **NOT VERIFIED** | `railway login` required |
| Token leak in API responses | **PASS** | No Bearer/JWT in council JSON bodies |

## VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| Vercel project has **no** environment variables | **VERIFIED** (`vercel env ls`) |
| Publishable key **not** in production JS bundle | **VERIFIED** (bundle probe post-redeploy) |
| Latest frontend deployed to `ben-v2.vercel.app` | **VERIFIED** (`vercel --prod`) |
| Backend auth flags and signed-out council smoke | **VERIFIED** |
| Signed-in Bearer headers | **NOT VERIFIED** (blocked) |
| `auth_missing` / `auth_valid` in Railway logs | **NOT VERIFIED** (CLI unauthorized) |

## 8. Git Status

- Branch: `main`
- Docs commit pending this session
- Local `.gitignore` has unstaged `.vercel` entry from `vercel link` (not committed per docs-only scope)

## 9. Risks / Warnings

| ID | Status | Notes |
|----|--------|-------|
| R-019 | **OPEN** | Railway logs not sampled |
| R-020 | **OPEN** | **VERIFIED MISSING** on Vercel; not closed until key set + signed-in E2E passes |
| R-013 | **PARTIAL** | Enforcement off; Bearer path unproven in prod until R-020 closed |

## 10. Manual step — add Clerk publishable key (do not paste key in chat)

From repo `frontend/` (project already linked):

```bash
cd frontend
vercel env add VITE_CLERK_PUBLISHABLE_KEY production
# Paste pk_test_... or pk_live_... when prompted (from Clerk Dashboard → API Keys)
vercel --prod --yes
```

Then verify (no key printed):

```bash
vercel env ls
python -c "import httpx,re; h=httpx.get('https://ben-v2.vercel.app').text; js=re.search(r'src=\"(/assets/[^\"]+\.js)\"',h).group(1); t=httpx.get('https://ben-v2.vercel.app'+js).text; print('pk_in_bundle', bool(re.search(r'pk_(live|test)_', t)))"
```

Browser: open `https://ben-v2.vercel.app` → Sign in → DevTools Network → confirm `Authorization: Bearer` on `/chat` and `/council` (redact token in screenshots). Confirm responses still HTTP 200 and shape unchanged.

Railway logs (after `railway login`):

```bash
railway logs --lines 200
# Look for: subsystem=auth, operation=shadow_auth_check, outcome=auth_missing|auth_valid, request_id
```

## 11. Recommended Next Step

1. Run `vercel env add` above, redeploy, repeat signed-in + bundle verification.
2. `railway login` → confirm `shadow_auth_check` lines (close R-019 if verified).
3. **Phase 3 tenant binding** before `ENFORCE_AUTH=true`.

## 12. Ready Status

**NOT READY** for signed-in production Bearer — Vercel key **VERIFIED MISSING**. Backend shadow mode **VERIFIED SAFE**.

---

## Current Phase

Frontend Bearer code on `main`; production Vercel build has **no** Clerk publishable key. Backend enforcement **off**.

## Vercel Clerk key

| Check | Status |
|-------|--------|
| `vercel env ls` | **MISSING** (VERIFIED) |
| Bundle `pk_*` | **MISSING** (VERIFIED post-redeploy) |

## Backend (production)

| Flag | Status |
|------|--------|
| `auth_enforcement` | false — VERIFIED |
| `auth_shadow_mode` | true — VERIFIED |

READY FOR CHATGPT REVIEW
