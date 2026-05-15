# BEN Risk Register

**Last register review:** 2026-05-15 (timing & load governance foundation)

**RISK_REGISTER.md changed:** YES

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual Railway dashboard audit | No | No |
| R-003 | Untracked `_council_test.json` and `scripts/` | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Commit `scripts/`; gitignore test JSON | No | No |
| R-008 | Structured logs without JSON formatter | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Formatter + metric extraction | No | No |
| R-009 | Timing & Load Governance | Medium | **IN_PROGRESS** | 2026-05-15 | 2026-05-15 | **CHANGED** — foundation docs on `feature/timing-load-governance-v1` | Merge docs; implement instrumentation (R-012) | No | No |
| R-010 | No runtime load isolation yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** | Enforce per-subsystem budgets per `TIMING_GOVERNANCE.md` | No | No |
| R-011 | No queue infrastructure yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** | Async persist / background council (T-107) | No | No |
| R-012 | No latency instrumentation yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** | Implement `INSTRUMENTATION_PLAN.md` phase 1 | No | No |

---

## ACCEPTED / DEFERRED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-004 | No formal PR for council-synthesis merge | Low | ACCEPTED | 2026-05-15 | 2026-05-15 | UNCHANGED | PRs going forward | No | No |
| R-006 | No Engineering OS automation yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-104 after timing docs merged | No | No |
| R-007 | No Dynamic Provider Config yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-106 | No | No |

---

## FIXED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Resolved | Notes |
|----|----------------|----------|--------|------------|--------------|----------|-------|
| R-001 | No `/health` in production | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `/health` 200. |
| R-005 | `/health` healthy path not integration-tested | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `/ready` 200. |

---

READY FOR CHATGPT REVIEW
