# TASK QUEUE

Execution ordering for BEN-V2. Update when tasks move between sections.

**Last updated:** 2026-05-15

---

## IN_PROGRESS

### T-108 Security Baseline (foundation v1 — docs)

| Field | Detail |
|-------|--------|
| **Branch** | `feature/security-baseline-v1` |
| **Goal** | `SECURITY_BASELINE.md`, `SECRETS_GOVERNANCE.md`; R-013–R-016 in risk register |
| **Dependencies** | Timing governance on `main` |
| **Verification** | Docs exist; no runtime/council shape changes |
| **Operational risks** | Docs-only; production remains unauthenticated until Phase 2 |

---

## READY

### T-108 Phase 1 — Secrets hygiene

| Field | Detail |
|-------|--------|
| **Goal** | `.gitignore` test JSON; optional `ENFORCE_AUTH` startup guard |
| **Dependencies** | Security baseline docs merged |
| **Verification** | `git status` clean; startup smoke |

### T-108 Phase 2 — Auth wiring

| Field | Detail |
|-------|--------|
| **Goal** | Clerk `get_current_user` on `/chat`, `/council` behind `ENFORCE_AUTH` |
| **Dependencies** | `CLERK_SECRET_KEY` in Railway; frontend sends Bearer |
| **Verification** | `401` without token when enforced; **200** with token; council shape unchanged |

### T-108 Phase 3 — Tenant binding

| Field | Detail |
|-------|--------|
| **Goal** | JWT `org_id` must match body `tenant_id` |
| **Dependencies** | Phase 2 |
| **Verification** | `403` on mismatch; persist uses correct org |

### Timeout budget alignment

| Field | Detail |
|-------|--------|
| **Goal** | Align `services/ops/timeouts.py` with `TIMING_GOVERNANCE.md` tiers |
| **Dependencies** | Timing docs on `main` |
| **Verification** | Local + prod smoke; no council shape change |

### T-104 Engineering OS Foundation

| Field | Detail |
|-------|--------|
| **Goal** | Branch/PR conventions, merge checklist, link `REPORT_TEMPLATE.md` |
| **Dependencies** | Timing governance docs merged |
| **Verification** | Checklist used on next feature merge |

### T-105 Operational Memory Layer

| Field | Detail |
|-------|--------|
| **Goal** | Persistent decisions/lessons in `docs/` |
| **Dependencies** | T-104 |
| **Verification** | `docs/DECISIONS.md` or equivalent |

### T-106 Dynamic Provider Config

| Field | Detail |
|-------|--------|
| **Goal** | Centralize provider/model env vars |
| **Dependencies** | Cost + timing governance |
| **Verification** | Env-only model change + `/council` smoke |

### T-107 Async Queue Infrastructure

| Field | Detail |
|-------|--------|
| **Goal** | Background jobs for persist / long council |
| **Dependencies** | R-011, timing enforcement |
| **Verification** | Design doc + time-boxed spike |

### R-003 hygiene — commit scripts

| Field | Detail |
|-------|--------|
| **Goal** | Commit `scripts/`, gitignore `_council_test.json` |
| **Dependencies** | None |
| **Verification** | CI or documented local verifier |

---

## BLOCKED

| ID | Task | Blocker |
|----|------|---------|
| — | Runtime timing enforcement | R-010; timeout code alignment not done |
| — | Load isolation at runtime | R-010 |

---

## COMPLETE

| ID | Task | Completed |
|----|------|-----------|
| T-101 | Reporting Foundation | On `feature/reporting-foundation-v1` |
| T-102 | Operational Health Layer | Merged via hardening (`/health`, `/ready`) |
| T-103 | Hardening Sprint v1 | Merged `main`; prod verified |
| T-109 | Timing & Load Governance docs | Merged `ac89049` to `main` |

---

READY FOR CHATGPT REVIEW
