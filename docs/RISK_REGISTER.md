# BEN Risk Register

**Last register review:** 2026-05-15 (runtime instrumentation merge + prod smoke)

**RISK_REGISTER.md changed:** YES

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Manual Railway dashboard audit | No | No |
| R-003 | Untracked `_council_test.json` and `scripts/` | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Commit `scripts/`; gitignore test JSON | No | No |
| R-008 | Structured logs without JSON formatter | Low | OPEN | 2026-05-15 | 2026-05-15 | **CHANGED** — code logs `duration_ms` in `extra`; Railway log tail **NOT VERIFIED** | Add lightweight JSON formatter for Railway | No | No |
| R-009 | Timing & Load Governance (docs only on branch) | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Merge `feature/timing-load-governance-v1` or fold into main | No | No |
| R-012 | Runtime latency instrumentation | Medium | **PARTIAL** | 2026-05-15 | 2026-05-15 | **CHANGED** — merged `4d39da9` to `main` (`2f016b8`); prod API smoke **PASS**; prod log field visibility **NOT VERIFIED** | JSON log formatter (R-008); optional `railway login` + log sample | No | No |

---

## ACCEPTED / DEFERRED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-004 | No formal PR for council-synthesis merge | Low | ACCEPTED | 2026-05-15 | 2026-05-15 | UNCHANGED | PRs going forward | No | No |
| R-006 | No Engineering OS automation yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-104 | No | No |
| R-007 | No Dynamic Provider Config yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-106 | No | No |

---

## FIXED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Resolved | Notes |
|----|----------------|----------|--------|------------|--------------|----------|-------|
| R-001 | No `/health` in production | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `/health` 200. |
| R-005 | `/health` healthy path not integration-tested | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `/ready` 200. |

---

READY FOR CHATGPT REVIEW
