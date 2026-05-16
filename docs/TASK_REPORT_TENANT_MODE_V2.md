# TASK REPORT — BEN Tenant Mode v2 (Personal + Organization Workspaces)

## 1. Task Name

BEN Tenant Mode v2 — Personal + Organization Workspaces

## 2. Branch

`feature/tenant-mode-v2` (not merged)

## 3. Goal

Allow signed-in users without Clerk `org_id` to use BEN in a **personal** workspace while preserving server-authoritative tenant binding and organization mode when `org_id` is present.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `auth/tenant_policy.py` | added |
| `auth/tenant_ids.py` | added |
| `auth/tenant_binding.py` | modified |
| `auth/config.py` | modified |
| `auth/org_errors.py` | modified (docstring) |
| `main.py` | modified — use `ctx.tenant_id` |
| `tests/test_tenant_modes_v2.py` | added |
| `tests/test_tenant_binding.py` | modified |
| `tests/test_clerk_org_recovery.py` | modified |
| `docs/RISK_REGISTER.md` | modified |
| `docs/TASK_REPORT_TENANT_MODE_V2.md` | added |

## 5. Code Changes

### Tenant derivation (JWT only)

| Auth state | `tenant_type` | `tenant_id` (DB/RLS) | `org_id` |
|------------|---------------|----------------------|----------|
| Signed in + `org_id` in JWT | `organization` | Clerk org UUID | same |
| Signed in, no org, `REQUIRE_ORG_FOR_SIGNED_IN=false` (default) | `personal` | UUID v5 of `user:{sub}` | `None` |
| Signed in, no org, `REQUIRE_ORG_FOR_SIGNED_IN=true` | — | 403 `clerk_org_required` | — |
| Signed out, `ENFORCE_AUTH=false` | `anonymous` | `BEN_ANONYMOUS_ORG_ID` | `None` |

Body `tenant_id` is never trusted; optional body field must match `ctx.tenant_id` when signed in.

### Policy flags

- `TENANT_MODES_ENABLED` (default `true`)
- `REQUIRE_ORG_FOR_SIGNED_IN` (default `false`)

### Health

`/health` and `/ready` auth checks expose `tenant_modes_enabled` and `require_org_for_signed_in`.

### Frontend

No code change required: org recovery banner only appears on `clerk_org_required` (403), which default policy no longer emits for signed-in users without org.

## 6. Verification Executed

```bash
git checkout -b feature/tenant-mode-v2
python -m pytest tests/test_tenant_modes_v2.py tests/test_tenant_binding.py tests/test_clerk_org_recovery.py tests/test_conversation_rehydration.py tests/test_council_lifecycle.py tests/test_council_gemini_strategy.py -q --tb=short
cd frontend && npm run build
```

Browser / production: **NOT EXECUTED**.

## 7. Verification Results

### Tenant mode matrix

| Scenario | Before | After |
|----------|--------|-------|
| Signed out | anonymous org | unchanged |
| Signed in + org | org tenant | unchanged |
| Signed in, no org | 403 + blocking banner | personal tenant; normal app use |
| Signed in, no org + `REQUIRE_ORG_FOR_SIGNED_IN=true` | N/A | 403 + banner (recovery v1) |
| Forged body `tenant_id` | 422 when signed | 422 (personal + org) |

### PASS / FAIL

| Check | Result | Notes |
|-------|--------|-------|
| `test_tenant_modes_v2.py` (10) | **PASS** | |
| `test_tenant_binding.py` | **PASS** | |
| `test_clerk_org_recovery.py` | **PASS** | org-required only when flag true |
| `test_conversation_rehydration.py` | **PASS** | |
| Council lifecycle + Gemini tests | **PASS** | |
| Frontend build | **PASS** | |
| Browser personal sign-in | **NOT VERIFIED** | |
| Prod deploy | **NOT VERIFIED** | |

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| Personal UUID isolation per `user_id` | **VERIFIED** (pytest) |
| Organization path unchanged | **VERIFIED** (pytest) |
| Anonymous path unchanged | **VERIFIED** (pytest) |
| Personal UX without org banner | **INFERRED** (no 403 by default) |
| Prod personal threads isolated | **NOT VERIFIED** |

## 8. Git Status

Branch `feature/tenant-mode-v2`; push to origin; **not merged**.

## 9. Risks / Warnings

- **R-014** — **PARTIAL** (personal + org forge pytest; prod signed not run)
- **R-026** — **PARTIAL** (personal thread API pytest; browser not run)
- **R-031** — **PARTIAL** (personal default; org-required policy opt-in)
- **R-032** — **OPEN** (new: personal/org naming and future billing/plan wiring)
- **R-015**, **R-019** — **OPEN** (unchanged)

Do not mark risks **FIXED** until browser verification.

## 10. Recommended Next Step

Merge after review → deploy → manual browser matrix (personal sign-in without org, org switcher with org, refresh rehydration per tenant type).

## 11. Ready Status

**READY FOR CHATGPT REVIEW** — automated tests **PASS**; browser/prod **NOT VERIFIED**.

---

READY FOR CHATGPT REVIEW
