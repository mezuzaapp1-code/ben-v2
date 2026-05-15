# TASK QUEUE

Execution ordering for BEN-V2. Update when tasks move between sections.

**Last updated:** 2026-05-15

---

## IN_PROGRESS

### Timing & Load Governance (foundation v1)

| Field | Detail |
|-------|--------|
| **Branch** | `feature/timing-load-governance-v1` |
| **Goal** | Document timing tiers, isolation, instrumentation plan, cost governance |
| **Dependencies** | Hardening v1 on `main` |
| **Verification** | `docs/TIMING_GOVERNANCE.md`, `INSTRUMENTATION_PLAN.md`, `COST_GOVERNANCE.md` exist; risk register updated |
| **Operational risks** | Docs-only; runtime still uninstrumented until R-012 closed |

---

## READY

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
| — | Runtime timing enforcement | R-012 instrumentation not implemented |
| — | Load isolation at runtime | R-010 |

---

## COMPLETE

| ID | Task | Completed |
|----|------|-----------|
| T-101 | Reporting Foundation | On `feature/reporting-foundation-v1` |
| T-102 | Operational Health Layer | Merged via hardening (`/health`, `/ready`) |
| T-103 | Hardening Sprint v1 | Merged `main`; prod verified |

---

READY FOR CHATGPT REVIEW
