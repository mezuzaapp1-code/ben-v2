# BEN Security Baseline

Minimum security posture for BEN-V2. **Define and document first; enforce in phased runtime work.** This document is the source of truth for auth, tenant isolation, request hardening, and operational security.

**Status:** Foundation v1 — documentation only (no new runtime enforcement in this phase).

---

## Core principles

| Principle | Meaning |
|-----------|---------|
| **Least privilege** | Services, routes, and DB access only what each path needs. |
| **Fail closed (when auth enabled)** | Missing or invalid credentials → `401`; no silent anonymous fallthrough on protected routes. |
| **Defense in depth** | Auth + tenant binding + rate limits + logging redaction + env hygiene. |
| **No secrets in the open** | Never return API keys, tokens, or full `DATABASE_URL` in HTTP bodies or `ben.ops` JSON logs. |
| **Tenant isolation** | Every mutating path must eventually bind `tenant_id` to an authenticated org, not client assertion alone. |
| **Observable, not leaky** | Security events are structured logs (`subsystem`, `operation`, `outcome`); payloads stay redacted. |
| **Degrade safely** | Security failures on optional layers must not corrupt council response shape or leak provider internals. |

---

## Threat model (initial)

| Threat | Exposure today | Target mitigation |
|--------|----------------|-------------------|
| Unauthenticated council/chat abuse | **High** — `POST /council`, `POST /chat` accept any caller | Clerk Bearer on mutating routes; optional service API key for automation |
| Cross-tenant data access | **High** — `tenant_id` supplied in JSON body without verification | Bind `tenant_id` to `org_id` from verified JWT |
| Cost / DoS via council | **Medium** — no rate limits; expensive multi-provider path | Per-IP and per-tenant rate limits; tier ceilings (`COST_GOVERNANCE.md`) |
| Secret exfiltration via logs/errors | **Low–Medium** — redaction exists; errors must stay sanitized | Extend redaction rules; audit `log_warning` call sites |
| CORS abuse from arbitrary origins | **Medium** — `https://*.vercel.app` wildcard | Explicit allowlist per environment |
| Health/ready reconnaissance | **Low** — public by design | No sensitive fields in probes; version string acceptable |

---

## Route classification

| Route | Purpose | Auth (today) | Auth (target) | Tenant binding (target) |
|-------|---------|--------------|---------------|---------------------------|
| `GET /health` | Liveness | None | None | N/A |
| `GET /ready` | Deploy readiness | None | None | N/A |
| `POST /chat` | User chat | **None** | Clerk Bearer required | `org_id` from JWT == `tenant_id` |
| `POST /council` | Council deliberation | **None** | Clerk Bearer required | `org_id` from JWT == `tenant_id` |
| `GET /docs`, `GET /openapi.json` | API explorer | None | Restrict in production (optional) | N/A |

**Existing code (not wired):** `auth/clerk_auth.py`, `auth/dependencies.py` (`get_current_user`). Integration is **Phase 2** below.

---

## Authentication policy

### Identity provider

- **Clerk** JWT verification via `CLERK_SECRET_KEY`.
- Bearer token in `Authorization` header: `Authorization: Bearer <jwt>`.

### Enforcement modes (future env)

| Mode | `ENFORCE_AUTH` | Behavior |
|------|----------------|----------|
| Development | `false` | Optional auth; warnings in logs if missing on mutating routes |
| Staging / Production | `true` | `401` on `/chat` and `/council` without valid Bearer |

Do **not** change response shapes on auth failure: return standard HTTP `401` JSON `{"detail": "..."}` only.

### Service-to-service (future)

- Optional `BEN_INTERNAL_API_KEY` header for smoke/automation only; never exposed to browser clients.
- Separate from Clerk; documented in `SECRETS_GOVERNANCE.md`.

---

## Tenant isolation policy

1. **Today:** `tenant_id` is a UUID string in request body; used for tracing, provider headers (`X-BEN-Tenant`), and KO persistence. **Not cryptographically bound to caller.**
2. **Target:** After Clerk verification, `tenant_id` in body must equal `org_id` from token (or mapped org table). Mismatch → `403` with generic message.
3. **Database:** RLS via `app.current_org_id` (see migration `001_initial_schema.py`). Session must set org context before writes — audit in Phase 3.

---

## Request hardening

| Control | Today | Target |
|---------|-------|--------|
| Request ID | `X-Request-ID` on `/health`, `/ready`, `/council` | Extend to `/chat` when traced |
| Body validation | Pydantic models on `/chat`, `/council` | Max question/message length caps |
| Body size limit | Starlette default | Explicit limit (e.g. 64 KB) on mutating routes |
| Timeouts | `services/ops/timeouts.py` | Align with `TIMING_GOVERNANCE.md` |
| Error sanitization | Council returns safe expert fallbacks | Never forward raw `HTTPStatusError` text to clients |
| Security headers | None | `X-Content-Type-Options`, `X-Frame-Options` on API responses (Phase 4) |

**Council response shape is frozen** unless explicitly versioned: `cost_usd`, `council`, `question`, `request_id`, `synthesis`.

---

## CORS policy

| Environment | Allowed origins (current) | Target |
|-------------|---------------------------|--------|
| Local | `http://localhost:5173` | Unchanged |
| Production | `https://ben-v2.vercel.app`, `https://*.vercel.app` | Named Vercel project URLs only; remove `*` subdomain wildcard |

Changes require frontend coordination; document in deploy notes before enforcement.

---

## Logging and audit

- **`ben.ops` JSON logs** — `BenOpsJsonFormatter` redacts keys matching `api_key`, `token`, `secret`, `password`, `database_url`, and string prefixes `sk-`, `sk_ant`, `Bearer `.
- **Do not log:** full `Authorization` headers, request bodies, provider API keys, or `DATABASE_URL`.
- **Do log (structured):** auth failures (`subsystem=security`, `operation=auth_verify`, `outcome=denied`), rate-limit hits, tenant mismatch (`category=auth_error`).

---

## Dependency and supply chain (future)

- Pin production dependencies in `requirements.txt`; review on each provider SDK bump.
- No secrets in repo; Railway/Vercel env only.
- Periodic `pip audit` or GitHub Dependabot — track under T-108.

---

## Implementation phases

| Phase | Deliverable | Runtime change? | Depends on |
|-------|-------------|-----------------|------------|
| **0 — Baseline docs (v1)** | `SECURITY_BASELINE.md`, `SECRETS_GOVERNANCE.md`, risk register | No | — |
| **1 — Secrets hygiene** | `.gitignore` test artifacts; startup warnings for missing `CLERK_SECRET_KEY` when `ENFORCE_AUTH=true` | Minimal | Phase 0 |
| **2 — Auth wiring** | `Depends(get_current_user)` on `/chat`, `/council`; feature-flagged | Yes | Clerk configured in prod |
| **3 — Tenant binding** | JWT `org_id` == body `tenant_id`; `403` on mismatch | Yes | Phase 2 |
| **4 — Rate limiting** | Per-IP / per-tenant middleware | Yes | Instrumentation (R-012) |
| **5 — CORS tighten + security headers** | Production allowlist | Yes | Frontend URLs fixed |
| **6 — RLS session audit** | Verify `current_org_id` on all DB writes | Yes | Phase 3 |

Phases 1–6 are **out of scope** for Security Baseline v1 documentation merge unless explicitly tasked.

---

## Verification checklist (per phase)

| Check | Phase |
|-------|-------|
| Docs exist on `main` | 0 |
| `POST /council` without token → `401` when `ENFORCE_AUTH=true` | 2 |
| Wrong `tenant_id` for JWT org → `403` | 3 |
| No `sk-` or `Bearer` in prod JSON logs sample | 1+ |
| Council smoke still **200** with valid token; shape unchanged | 2+ |
| `/health`, `/ready` remain unauthenticated | 2+ |

---

## Related documents

| Doc | Topic |
|-----|--------|
| `SECRETS_GOVERNANCE.md` | Env classification, rotation, commit hygiene |
| `TIMING_GOVERNANCE.md` | Timeouts and DoS-related bounded execution |
| `COST_GOVERNANCE.md` | Abuse cost ceilings |
| `INSTRUMENTATION_PLAN.md` | Security metric hooks (future) |
| `docs/RISK_REGISTER.md` | R-013–R-015 |

---

READY FOR CHATGPT REVIEW
