# BEN Risk Register

**Last register review:** 2026-05-15 (post-hardening cleanup)

**RISK_REGISTER.md changed:** YES

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | **CHANGED** — prod behavior OK; CLI still unauthorized; local `.env` all required keys present | Manual dashboard audit (see STATUS_REPORT Railway steps); optional `railway login` | No | No |
| R-003 | Untracked `_council_test.json` and `scripts/` | Low | OPEN | 2026-05-15 | 2026-05-15 | **CHANGED** — classified: commit `scripts/`; gitignore `_council_test.json` | Commit `scripts/verify_council_prerelease.py` + `run_council_merge_checks.ps1`; add `_council_test.json` to `.gitignore` | No | No |
| R-008 | Structured logs use `logging.extra` without custom formatter | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | JSON log formatter or Railway log mapping | No | No |
| R-009 | No Timing & Load Governance yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | **NEW** | Design rate limits, council timeouts budget, cost caps before scale | No | No |

---

## ACCEPTED / DEFERRED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-004 | No formal PR record for council-synthesis merge | Low | ACCEPTED | 2026-05-15 | 2026-05-15 | UNCHANGED | Use PRs going forward | No | No |
| R-006 | No Engineering OS automation yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-104 after timing governance | No | No |
| R-007 | No Dynamic Provider Config yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | T-106 | No | No |

---

## FIXED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Resolved | Notes |
|----|----------------|----------|--------|------------|--------------|----------|-------|
| R-001 | No `/health` endpoint in production yet | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `GET /health` **200**, `healthy`, `request_id`. |
| R-005 | Healthy path for `/health` not live integration-tested yet | Medium | FIXED | 2026-05-15 | 2026-05-15 | **2026-05-15** | Prod `GET /ready` **200**, `ready=true`, `migration_head=002_ko_synthesis_jsonb`. |

---

READY FOR CHATGPT REVIEW
