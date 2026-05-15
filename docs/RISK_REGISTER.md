# BEN Risk Register

**Last register review:** 2026-05-15 (Reasoning preservation v1 — branch, not merged)

**RISK_REGISTER.md changed:** YES

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual Railway dashboard audit | No | No |
| R-010 | No runtime load isolation yet | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | UNCHANGED | Per-tenant concurrency / queues | No | No |
| R-011 | No queue infrastructure yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-107 | No | No |
| R-012 | Runtime latency instrumentation | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | UNCHANGED | Prod JSON log sample | No | No |
| R-013 | Unauthenticated `/chat` and `/council` | **High** | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — Phase A: API leakage + `request_id` **VERIFIED**; signed-in Bearer + `auth_valid` logs **NOT VERIFIED** | Manual DevTools Bearer check; `railway logs`; tenant binding; then enforce | No | **Yes** (until enforce) |
| R-014 | Client-supplied `tenant_id` without auth binding | **High** | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Phase 3 tenant binding | No | **Yes** (cross-tenant) |
| R-015 | No rate limiting on expensive routes | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-108 Phase 4 | No | No |
| R-018 | Accidental shell artifact files in repo root | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual delete locally | No | No |
| R-019 | Auth shadow without production log baseline | Low | OPEN | 2026-05-15 | 2026-05-15 | **CHANGED** — prod traffic for all shadow outcomes **SENT** (unsigned, invalid, valid JWT); `railway logs` **NOT VERIFIED** (CLI unauthorized in agent env) | Local: `railway login` → `verify_r019_production_logs.py` | No | No |
| R-022 | Multi-provider council response divergence | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — synthesis v1 adds domain reasoning sections; differentiation **PARTIALLY VERIFIED** (pytest); prod open | Monitor LLM compliance + council UX | No | No |
| R-023 | Gemini Strategy Advisor operational variability | Low–Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — prod Strategy `google`/`gemini-2.5-flash`/`ok` **VERIFIED**; `gemini-1.5-flash` **FAIL** (retired) | Pin `GEMINI_MODEL=gemini-2.5-flash` on Railway | No | No |
| R-024 | Council synthesis compresses distinct expert reasoning | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** — optional `legal_reasoning`/`strategic_reasoning`/etc.; pytest **NOT PROD VERIFIED** until merge | Merge `feature/reasoning-preservation-v1` + prod spot-check | No | No |

---

## ACCEPTED / DEFERRED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-004 | No formal PR for council-synthesis merge | Low | ACCEPTED | 2026-05-15 | 2026-05-15 | UNCHANGED | PRs going forward | No | No |
| R-006 | No Engineering OS automation yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-104 | No | No |
| R-007 | No Dynamic Provider Config yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-106 | No | No |
| R-016 | CORS wildcard `https://*.vercel.app` | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-108 Phase 5 | No | No |

---

## FIXED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Resolved | Notes |
|----|----------------|----------|--------|------------|--------------|----------|-------|
| R-001 | No `/health` in production | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `/health` 200. |
| R-005 | `/health` healthy path not integration-tested | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `/ready` 200. |
| R-008 | Structured logs without JSON formatter | Low | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | `BenOpsJsonFormatter` | — |
| R-009 | Timing & Load Governance (docs only) | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Docs + runtime timeouts | — |
| R-003 | Untracked scripts / test JSON clutter | Low | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Hygiene merge | — |
| R-017 | Council worst-case may exceed 25s DELIBERATE | Low–Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Outer 25s cap on prod | — |
| R-020 | Frontend deploy without Clerk publishable key | Low | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Vercel env + bundle `pk_*` + sign-in UI **VERIFIED**; signed-in Bearer header E2E optional | — |
| R-021 | Council synthesis may overstate agreement when experts degrade | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Merged `e0c056c`; prod API `provider`/`outcome` **VERIFIED**; degraded path pytest **VERIFIED**; Vercel UI redeployed | — |

---

READY FOR CHATGPT REVIEW
