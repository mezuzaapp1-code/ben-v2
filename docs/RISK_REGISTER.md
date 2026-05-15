# BEN Risk Register

**Last register review:** 2026-05-15 (hardening sprint)

**RISK_REGISTER.md changed:** YES

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-001 | No `/health` endpoint in production yet | Medium | IN_PROGRESS | 2026-05-15 | 2026-05-15 | **CHANGED** — implemented on `feature/hardening-v1` (includes health layer); prod deploy pending | Merge hardening or operational-health; verify prod `/health` `/ready` | No | No |
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Confirm in Railway UI | No | No |
| R-003 | Untracked local files `_council_test.json` and `scripts/` | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Commit or gitignore | No | No |
| R-005 | Healthy path for `/health` not live integration-tested yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | **CHANGED** — degraded path verified; healthy 200 still needs reachable DB post-deploy | Re-verify after merge/deploy | No | No |
| R-008 | Structured logs use `logging.extra` without custom formatter | Low | OPEN | 2026-05-15 | 2026-05-15 | **NEW** | Add JSON log formatter or Railway log drain mapping | No | No |

---

## ACCEPTED / DEFERRED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-004 | No formal PR record for council-synthesis merge | Low | ACCEPTED | 2026-05-15 | 2026-05-15 | UNCHANGED | Use PRs going forward | No | No |
| R-006 | No Engineering OS automation yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-104 | No | No |
| R-007 | No Dynamic Provider Config yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-106 | No | No |

---

## FIXED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Resolved | Notes |
|----|----------------|----------|--------|------------|--------------|----------|-------|
| — | *(none yet)* | — | — | — | — | — | |

---

READY FOR CHATGPT REVIEW
