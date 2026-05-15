# BEN TASK REPORT — Tenant Binding v1

**Last updated:** 2026-05-15

## 1. Task Name

Server-authoritative tenant identity for `/chat` and `/council` using Clerk JWT claims (`org_id` / nested `o.id`); client JSON tenancy fields untrusted.

## 2. Branch

`feature/tenant-binding-v1` (not merged)

## 3. Before / after authority flow

**Before:** `tenant_id` from request body was passed through to RLS / provider headers and could be spoofed on unsigned traffic.

**After:** `TenantContext` is built only from verified JWT when `Authorization: Bearer` is valid; unsigned traffic uses `BEN_ANONYMOUS_ORG_ID` (default `00000000-0000-0000-0000-000000000001`). Optional body `tenant_id` is **ignored** when anonymous; when JWT-bound, mismatched body `tenant_id` returns **422**. Unknown JSON keys (`org_id`, etc.) return **422** (`extra="forbid"`). `ENFORCE_AUTH` remains default **false**.

## 4. Verification commands

```bash
python -m pytest tests/test_tenant_binding.py tests/test_council_degraded_honesty.py tests/test_council_gemini_strategy.py tests/test_reasoning_preservation.py -v
cd frontend && npm run build
```

## 5. Forged tenant test (pytest)

| Case | Expected | Result |
|------|----------|--------|
| JWT org A + body `tenant_id` B | 422 | **PASS** |
| Unsigned + arbitrary body `tenant_id` | Server uses anonymous org | **PASS** |
| JWT org A + omitted body `tenant_id` | Handler receives org A | **PASS** |

## 6. PASS / FAIL

| Check | Result |
|-------|--------|
| JWT org resolution + mismatch 422 | **PASS** |
| Anonymous ignores forged body tenant | **PASS** |
| Extra JSON fields forbidden | **PASS** |
| `ENFORCE_AUTH=true` invalid JWT → 401 | **PASS** |
| JWT valid, org missing → 400 | **PASS** |
| `/health` checks: `tenant_binding_enabled`, `enforce_auth` | **PASS** |
| Council/chat response shapes unchanged | **PASS** (mocked) |
| Production signed smoke | **NOT VERIFIED** |

## 7. VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| Cross-tenant body forgery blocked when signed | **VERIFIED** (pytest) |
| Production Clerk org claim shape / UX | **INFERRED** |

## 8. Risks

| ID | Status |
|----|--------|
| R-014 | **PARTIAL** — pytest only; **not FIXED** until prod |
| R-019 | **OPEN** — extend log review for `tenant_bind` |

## 9. Readiness

**READY FOR REVIEW** — deploy + prod signed request with forged body to close R-014.

---

READY FOR CHATGPT REVIEW
