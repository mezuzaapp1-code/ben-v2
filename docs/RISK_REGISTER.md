# BEN Risk Register

**Last register review:** 2026-05-15 (hardening merge + production smoke)

**RISK_REGISTER.md changed:** YES

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Confirm in Railway UI or `railway login` | No | No |
| R-003 | Untracked local files `_council_test.json` and `scripts/` | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Commit useful scripts or add to `.gitignore` | No | No |
| R-008 | Structured logs use `logging.extra` without custom formatter | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Add JSON log formatter or Railway log drain mapping | No | No |

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
| R-001 | No `/health` endpoint in production yet | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Merged `feature/hardening-v1` → `main` (`b7a4d85`). Prod smoke: `GET /health` **200**, `request_id` present, `status=healthy`, `database=ok`. |
| R-005 | Healthy path for `/health` not live integration-tested yet | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod smoke: `GET /ready` **200**, `ready=true`, `migration_head=002_ko_synthesis_jsonb`. Healthy path verified against Railway DB. |

---

READY FOR CHATGPT REVIEW
