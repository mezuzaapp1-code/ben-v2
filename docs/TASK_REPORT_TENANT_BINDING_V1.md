# TASK REPORT

## 1. Task Name

BEN Tenant Binding v1 — Clerk JWT–authoritative org for `/chat` and `/council`.

## 2. Branch

`feature/tenant-binding-v1`

## 3. Goal

Close R-014: never trust `tenant_id` / `org_id` from JSON; derive org from verified Clerk JWT when signed; keep unsigned behavior under `ENFORCE_AUTH=false` using server `BEN_ANONYMOUS_ORG_ID`; expose safe flags on `/health` / `/ready`; log tenant bind metadata without JWTs.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `auth/tenant_binding.py` | added |
| `auth/shadow_auth.py` | modified |
| `auth/config.py` | modified |
| `main.py` | modified |
| `frontend/src/App.jsx` | modified |
| `.env.example` | modified |
| `tests/test_tenant_binding.py` | added |
| `docs/RISK_REGISTER.md` | modified |
| `docs/STATUS_REPORT.md` | modified |
| `docs/TASK_REPORT_TENANT_BINDING_V1.md` | added |

## 5. Code Changes

- `TenantContext` dataclass: `org_id`, `user_id`, `email`, `auth_source` (`clerk_jwt` \| `anonymous`), `auth_present`, `org_bound`.
- `authenticate_from_authorization` / `authenticate_request`: single Clerk verify path; returns claims on `auth_valid`.
- `/chat` / `/council`: after `apply_auth_policy`, build context, validate optional body `tenant_id` vs JWT org, `log_tenant_bound`, call handlers with `ctx.org_id` and `ctx.user_id or "anonymous"`.
- Pydantic `extra="forbid"` rejects client `org_id` / `tenant` keys.
- Health checks: `tenant_binding_enabled`, `enforce_auth` (alias of `auth_enforcement` boolean).

## 6. Verification Executed

```bash
python -m pytest tests/test_tenant_binding.py tests/test_council_degraded_honesty.py tests/test_council_gemini_strategy.py tests/test_reasoning_preservation.py -v
cd frontend && npm run build
```

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| Forged body vs JWT org | **PASS** | 422 |
| Unsigned + wrong body tenant | **PASS** | anonymous org only |
| Council shape | **PASS** | mocked `run_council` |
| Health flags | **PASS** | |
| Prod Clerk | **NOT VERIFIED** | |

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| Authority boundary for signed users | **VERIFIED** (pytest) |
| Real Clerk session includes `org_id` | **INFERRED** |

## 8. Git Status

Branch `feature/tenant-binding-v1`; commit and push per user instruction.

## 9. Risks / Warnings

- Users with valid JWT but **no org in token** get **400** until Clerk session supplies org.
- Anonymous traffic still hits shared `BEN_ANONYMOUS_ORG_ID` DB scope until `ENFORCE_AUTH` is enabled.
- Scripts that send extra JSON fields may need updates (forbidden keys).

## 10. Recommended Next Step

Production smoke: signed JWT org A + body `tenant_id` org B → expect **422**; unsigned → **200** with shared anonymous org.

## 11. Ready Status

**READY WITH WARNINGS** — R-014 remains **PARTIAL** until prod verification.

---

READY FOR CHATGPT REVIEW
