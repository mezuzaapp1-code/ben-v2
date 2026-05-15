# TASK QUEUE

Execution ordering and operational continuity for BEN-V2. Move tasks between sections as work progresses.

**Last updated:** 2026-05-15

---

## READY

### T-101 Reporting Foundation

| Field | Detail |
|-------|--------|
| **Goal** | Standardize engineering reports, status tracking, and task queue under `docs/`. |
| **Dependencies** | None |
| **Verification** | Files exist; template sections complete; commit on `feature/reporting-foundation-v1`; TASK REPORT generated from template |
| **Operational risks** | Stale docs if not updated after each release — mitigate by updating `STATUS_REPORT.md` per task |

### T-102 Operational Health Layer

| Field | Detail |
|-------|--------|
| **Goal** | Add `GET /health` (and optional readiness) for Railway/monitor liveness; document in smoke plan |
| **Dependencies** | T-101 (reporting) recommended |
| **Verification** | `GET /health` → 200 locally and in production; update smoke checklist in reports |
| **Operational risks** | False positives if health does not check DB — document scope (liveness vs readiness) |

### T-103 Hardening Sprint v1

| Field | Detail |
|-------|--------|
| **Goal** | Council/synthesis edge cases: timeouts, logging, commit untracked `scripts/verify_council_prerelease.py` or CI equivalent |
| **Dependencies** | T-101 |
| **Verification** | Mocked verifier in CI or documented local script; failure isolation re-run |
| **Operational risks** | Flaky external APIs in CI — prefer HTTP mocks |

### T-104 Engineering OS Foundation

| Field | Detail |
|-------|--------|
| **Goal** | Repo conventions: branch naming, merge checklist, link `REPORT_TEMPLATE.md` to PR/process |
| **Dependencies** | T-101 |
| **Verification** | Checklist used on next feature merge; report references template |
| **Operational risks** | Process drift without enforcement |

### T-105 Operational Memory Layer

| Field | Detail |
|-------|--------|
| **Goal** | Persistent decisions/lessons (e.g. Anthropic model IDs, deploy-from-main rule) in `docs/` |
| **Dependencies** | T-101, T-104 |
| **Verification** | ADR or `docs/DECISIONS.md` entries for prod incidents |
| **Operational risks** | Duplication with `STATUS_REPORT.md` — cross-link |

### T-106 Dynamic Provider Config

| Field | Detail |
|-------|--------|
| **Goal** | Centralize provider/model env vars (OpenAI, Anthropic, synthesis) — avoid scattered hardcodes |
| **Dependencies** | Production stable (council-synthesis on `main`) |
| **Verification** | Env-only model change without code deploy; `/council` smoke |
| **Operational risks** | Misconfigured Railway vars break Legal or synthesis |

### T-107 Async Queue Infrastructure

| Field | Detail |
|-------|--------|
| **Goal** | Background jobs for long council runs / persistence (design spike) |
| **Dependencies** | T-103, T-106 |
| **Verification** | Design doc + optional spike; not required for v1 reporting |
| **Operational risks** | Scope creep — keep spike time-boxed |

---

## IN_PROGRESS

| ID | Task | Owner / notes |
|----|------|----------------|
| T-101 | Reporting Foundation | Active on `feature/reporting-foundation-v1` |

---

## BLOCKED

| ID | Task | Blocker |
|----|------|---------|
| — | — | — |

---

## COMPLETE

| ID | Task | Completed |
|----|------|-----------|
| — | Council synthesis + prod deploy | 2026-05-15 — merged `feature/council-synthesis-v1` → `main`, smoke passed |

---

## Queue rules

1. One **IN_PROGRESS** task per branch when possible.
2. Update **STATUS_REPORT.md** when moving tasks to COMPLETE.
3. Every COMPLETE task should have a TASK REPORT (see `REPORT_TEMPLATE.md`).
4. Do not mark COMPLETE without verification executed or explicitly NOT VERIFIED with reason.

---

READY FOR CHATGPT REVIEW
