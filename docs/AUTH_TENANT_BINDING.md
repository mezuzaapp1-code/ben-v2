# Auth & Tenant Binding (future)

**Status:** Documentation + TODO markers only — **not enforced** in T-108 Phase 2.

---

## Current state (unsafe compatibility)

- `POST /chat` and `POST /council` accept `tenant_id` in the JSON body.
- Callers are **not** required to prove ownership of that tenant.
- **R-014 remains OPEN.**

---

## Target state (Phase 3)

1. Valid Clerk Bearer token required when `ENFORCE_AUTH=true`.
2. `tenant_id` in body must match `org_id` from verified JWT claims.
3. Mismatch → `403` with generic message (no token or org details in response).
4. Database RLS session uses verified org only.

---

## TODO markers in code

- `auth/shadow_auth.py` — `# TODO(R-014): bind body tenant_id to verified org_id`

---

## Shadow mode (Phase 2)

- `ENFORCE_AUTH=false` (default) — no blocking.
- `AUTH_SHADOW_MODE=true` (default) — log `auth_missing` / `auth_valid` / `auth_invalid` / `auth_error`.
- Observe traffic before enforcement.

---

READY FOR CHATGPT REVIEW
