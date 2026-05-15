# BEN Risk Register

**Last register review:** 2026-05-15 (R-017 outer council timeout cap branch)

**RISK_REGISTER.md changed:** YES

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual Railway dashboard audit | No | No |
| R-010 | No runtime load isolation yet | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | UNCHANGED | Per-tenant concurrency / queues | No | No |
| R-011 | No queue infrastructure yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-107 | No | No |
| R-012 | Runtime latency instrumentation | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | UNCHANGED | `railway login` → prod JSON log sample | No | No |
| R-013 | Unauthenticated `/chat` and `/council` | **High** | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-108 Phase 2 | No | **Yes** (prod abuse) |
| R-014 | Client-supplied `tenant_id` without auth binding | **High** | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-108 Phase 3 | No | **Yes** (cross-tenant) |
| R-015 | No rate limiting on expensive routes | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | T-108 Phase 4 | No | No |
| R-018 | Accidental shell artifact files in repo root | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual delete locally | No | No |

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
| R-009 | Timing & Load Governance (docs only) | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Docs + runtime timeouts `b798e05` | — |
| R-003 | Untracked scripts / test JSON clutter | Low | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | `0bbc449` hygiene merge | — |
| R-017 | Council worst-case may exceed 25s DELIBERATE | Low–Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | `COUNCIL_TOTAL_TIMEOUT_S=25`; partial fallback on outer timeout; local verify | — |

---

READY FOR CHATGPT REVIEW
