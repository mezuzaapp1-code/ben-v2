# TASK REPORT

## 1. Task Name

BEN Clerk Organization Recovery v1

## 2. Branch

`fix/clerk-org-context-recovery-v1`

## 3. Goal

Recover gracefully when a signed-in Clerk user has no organization in the JWT: structured backend error (no silent anonymous org fallback), humanized frontend UX with org switcher and recoverable banner, robust thread rehydration without wiping local state, tests, and risk register updates—without weakening tenant binding.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `auth/org_errors.py` | added |
| `auth/tenant_binding.py` | modified |
| `frontend/src/api/benErrors.js` | added |
| `frontend/src/api/threads.js` | modified |
| `frontend/src/App.jsx` | modified |
| `frontend/src/App.css` | modified |
| `frontend/scripts/test-ben-errors.mjs` | added |
| `tests/test_clerk_org_recovery.py` | added |
| `tests/test_tenant_binding.py` | modified |
| `docs/RISK_REGISTER.md` | modified |
| `docs/TASK_REPORT_CLERK_ORG_RECOVERY.md` | added |

## 5. Code Changes

- **Backend:** Valid JWT without `org_id` raises **403** with structured `detail`: `code: clerk_org_required`, `message`, `hint`, `recoverable: true`. No fallback to `BEN_ANONYMOUS_ORG_ID` for signed users (prevents mixing signed user history with anonymous org).
- **Frontend:** `parseBenErrorResponse` / `humanizeBenHttpError` strip raw JSON; chat/council/hydration show `OrgRecoveryBanner` and Clerk `OrganizationSwitcher` when signed in; API errors use `kind: api_error` styling.
- **Rehydration:** On `clerk_org_required`, local active thread and draft messages are retained; banner + retry after org selection.
- **Anonymous:** Unsigned requests unchanged—anonymous org from env only; body `tenant_id` still ignored/untrusted per tenant binding v1.

## 6. Verification Executed

### Git / repo

```bash
git branch --show-current
git status -sb
```

### Backend

```bash
python -m pytest tests/test_clerk_org_recovery.py tests/test_tenant_binding.py -q --tb=short
```

### Frontend

```bash
cd frontend && npm run build
node frontend/scripts/test-ben-errors.mjs
```

### Production smoke / browser

**NOT EXECUTED** in this session (R-031 remains OPEN until manual browser verification).

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| pytest clerk org + tenant binding (19) | PASS | Includes 403 structured, anonymous, signed+org, forged body 422, GET threads 403 |
| Frontend production build | PASS | Vite build OK |
| benErrors humanization script | PASS | No `{` in humanized clerk org message |
| Signed-in without org — browser UX | NOT VERIFIED | Requires Clerk session without active org |
| Signed-in with org — browser | NOT VERIFIED | |
| Signed-out anonymous — browser | NOT VERIFIED | pytest covers API |
| Prod deploy | NOT VERIFIED | Branch pushed; not merged |

### Auth state matrix

| State | ENFORCE_AUTH=false | Org in JWT | Backend tenant | Chat/Council | Threads API |
|-------|-------------------|------------|----------------|--------------|-------------|
| Signed out | yes | n/a | `BEN_ANONYMOUS_ORG_ID` | 200 (mocked) | anonymous scope |
| Signed in + org | yes | yes | JWT `org_id` | 200 (mocked) | org scope |
| Signed in, no org | yes | no | **403** `clerk_org_required` | **403** (handler not called) | **403** |
| Signed in + forged body tenant | yes | yes | JWT only; body mismatch | **422** | — |

### Before / after

| Scenario | Before | After |
|----------|--------|-------|
| Signed in, no org, POST /chat | 400 string detail → raw JSON in bubble | 403 structured; banner + org switcher; no provider call |
| Signed in, no org, hydrate | Error could disrupt UX | Banner; local thread kept |
| Signed out | Anonymous org | Unchanged |

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| 403 + `clerk_org_required` on missing org (pytest) | VERIFIED |
| No silent anonymous fallback for signed user | VERIFIED (code + pytest) |
| Forged body tenant rejected with JWT org | VERIFIED (pytest) |
| Frontend no raw JSON for structured 403 | VERIFIED (node script + code review) |
| OrganizationSwitcher fixes prod without manual Clerk dashboard | INFERRED |
| Prod signed-in-no-org UX | NOT VERIFIED |

## 8. Git Status

Branch: `fix/clerk-org-context-recovery-v1`. Commit and push performed at end of task (see session log). Not merged to `main`.

## 9. Risks / Warnings

- **R-014** remains **PARTIAL** (signed JWT + forged body prod test not run).
- **R-026** remains **PARTIAL** (refresh E2E not verified).
- **R-031** **OPEN** until browser verification of signed-in-no-org path.
- Users must select a Clerk org or sign out for anonymous mode; no automatic org assignment.

## 10. Recommended Next Step

1. Deploy branch preview or merge after review.
2. Manual browser matrix: signed-out chat/council; signed-in with org; signed-in without org (confirm banner, switcher, no raw JSON, retry works).
3. Close R-031 to **FIXED** only after that verification.

## 11. Ready Status

**READY FOR CHATGPT REVIEW** — pytest and build PASS; browser/prod org-recovery UX **NOT VERIFIED**.

---

READY FOR CHATGPT REVIEW
