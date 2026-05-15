# BEN TASK REPORT — Auth Verification Phase A

**Last updated:** 2026-05-15

## 1. Task Name

Auth Verification Phase A — signed-in Bearer flow, leakage checks, Railway `shadow_auth_check` baseline (before tenant binding or enforcement).

## 2. Branch

`main` @ `34acb73` (prior); Phase A docs commit pending.

## 3. Goal

Fully verify signed-in Bearer authentication in production while `ENFORCE_AUTH=false`. Confirm no token leakage and `request_id` propagation. Close **R-019** only if `auth_valid` observed in production logs.

**Out of scope:** tenant binding, rate limiting, `ENFORCE_AUTH=true`, council response shape changes.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `docs/STATUS_REPORT.md` | modified |
| `scripts/verify_auth_phase_a.py` | added |
| `scripts/parse_railway_shadow_auth_logs.py` | added |

## 5. Code Changes

None to runtime auth policy. Added verification scripts only.

## 6. Verification Executed

```bash
python scripts/verify_auth_phase_a.py
python scripts/verify_auth_shadow_v1.py https://ben-v2-production.up.railway.app
python scripts/probe_vercel_clerk_bundle.py
python scripts/verify_frontend_bearer_e2e.py
railway whoami
railway login --browserless          # requires interactive terminal or RAILWAY_TOKEN
railway logs --lines 200             # NOT EXECUTED (unauthorized)
```

Signed-in Bearer (DevTools): **manual checklist below** — agent cannot complete without interactive Clerk login or `CLERK_TEST_*` env vars.

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| Vercel bundle `pk_*` | **PASS** | `publishable_key_in_bundle=PRESENT` |
| Sign-in button visible | **PASS** | Playwright |
| `Authorization: Bearer` on POST `/chat` (signed-in) | **NOT VERIFIED** | Manual / test creds required |
| `Authorization: Bearer` on POST `/council` (signed-in) | **NOT VERIFIED** | Manual / test creds required |
| No JWT in API response bodies | **PASS** | `verify_auth_phase_a.py` + shadow script |
| Invalid Bearer non-blocking | **PASS** | HTTP 200, shape unchanged |
| `auth_enforcement=false` | **PASS** | `/health` |
| Council `request_id` present | **PASS** | Signed-out prod council |
| Chat `thread_id` present | **PASS** | Signed-out prod chat |
| `shadow_auth_check` in Railway logs | **NOT VERIFIED** | CLI unauthorized |
| `auth_missing` in prod logs | **NOT VERIFIED** | — |
| `auth_invalid` in prod logs | **NOT VERIFIED** | — |
| `auth_valid` in prod logs | **NOT VERIFIED** | — |
| **R-019** | **OPEN** | Not closed — `auth_valid` not verified in prod logs |

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| Shadow auth code logs `outcome` only (no token in `shadow_auth.py`) | **VERIFIED** (code review) |
| `request_id` on council JSON responses | **VERIFIED** (prod API) |
| `thread_id` on chat JSON responses | **VERIFIED** (prod API) |
| Signed-in Bearer headers | **NOT VERIFIED** |
| Prod log outcomes `auth_missing` / `auth_invalid` / `auth_valid` | **NOT VERIFIED** |

---

## 8. Manual verification — signed-in Bearer (required to complete Phase A)

1. Open **https://ben-v2.vercel.app** (incognito optional).
2. **Sign in** via Clerk (sidebar **Sign in**).
3. Open **DevTools → Network** → filter `Fetch/XHR`.
4. Send a normal message → find **POST** to `ben-v2-production.up.railway.app/chat` (or proxied `/chat`).
   - **Request Headers:** confirm `Authorization: Bearer …` exists.
   - **Do not** copy the token into tickets, chat, or logs.
5. Click **Council** with a question → find **POST** `/council`.
   - Confirm **`Authorization: Bearer`** again.
6. **Console tab:** confirm no full JWT / Bearer string logged (redact if screenshotting).
7. **Response tab:** confirm JSON shape unchanged; no `eyJ…` JWT in body.

### Pass criteria

| Item | Expected |
|------|----------|
| `/chat` status | 200 |
| `/council` status | 200 |
| Council keys | `cost_usd`, `council`, `question`, `request_id`, `synthesis` |
| Chat keys | `cost_usd`, `model_used`, `response`, `thread_id` |

### Optional automation

```bash
set CLERK_TEST_EMAIL=...
set CLERK_TEST_PASSWORD=...
python scripts/verify_frontend_bearer_e2e.py
```

Expect: `chat_authorization_bearer=PRESENT`, `council_authorization_bearer=PRESENT`.

---

## 9. Railway log verification (required for R-019)

```bash
railway login
railway link          # if needed — select BEN production service
railway logs --lines 200 > railway_shadow_sample.log
python scripts/parse_railway_shadow_auth_logs.py railway_shadow_sample.log
```

**Look for JSON lines** with:

- `"subsystem": "auth"`
- `"operation": "shadow_auth_check"`
- `"outcome": "auth_missing"` | `"auth_invalid"` | `"auth_valid"`
- `"request_id": "<uuid>"` on the same request context

**Fail if:** any log line contains a full JWT (`eyJ…`).

After manual sign-in + council/chat traffic, re-run logs and confirm at least one `auth_valid` line before closing **R-019**.

---

## 10. Structured log design (no token leakage)

`auth/shadow_auth.py` classifies auth without logging the Authorization header or token. `log_shadow_auth_check` emits `subsystem=auth`, `operation=shadow_auth_check`, `outcome` only. `structured_log._base_extra` attaches `request_id` from request context when present.

---

## 11. Risks

| ID | Status |
|----|--------|
| R-019 | **OPEN** — prod shadow log baseline not CLI-verified |
| R-013 | **PARTIAL** — enforcement off; signed-in Bearer E2E incomplete |
| R-014 | **OPEN** — tenant binding deferred |
| R-020 | **FIXED** — Clerk publishable key on Vercel |

---

## 12. Recommended Next Step

1. Complete **manual DevTools** Bearer check (Section 8).
2. `railway login` → log sample → `parse_railway_shadow_auth_logs.py` → close **R-019** if `auth_valid` confirmed.
3. **Phase B:** tenant binding (`tenant_id` ↔ JWT `org_id`) — still before `ENFORCE_AUTH=true`.

## 13. Ready Status

**NOT READY** for Phase B / enforcement — signed-in Bearer headers and prod `auth_valid` logs **not verified** in this session.

---

READY FOR CHATGPT REVIEW
