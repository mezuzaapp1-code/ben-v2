# BEN Risk Register

Persistent issue and risk ledger for BEN-V2. Update **Last Checked** and **Changed Since Last Report** when reviewing risks during task reports.

**Rules**

- Every task report must state whether `RISK_REGISTER.md` changed (or **UNCHANGED** if not).
- Do not delete risks silently — move resolved items to **FIXED**.
- Separate **ACTIVE**, **ACCEPTED / DEFERRED**, and **FIXED** sections.
- **Blocks Merge?** / **Blocks Deploy?** — `Yes` only when the risk must block that gate; otherwise `No`.

**Last register review:** 2026-05-15

---

## ACTIVE

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-001 | No `/health` endpoint in production yet | Medium | IN_PROGRESS | 2026-05-15 | 2026-05-15 | UNCHANGED | Merge/deploy `feature/operational-health-v1`; verify production `GET /health` and `GET /ready` return expected status | No | No |
| R-002 | Railway variables not CLI-verified | Low–Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Confirm in Railway UI or `railway login` + `railway variables` | No | No |
| R-003 | Untracked local files `_council_test.json` and `scripts/` | Low | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Commit useful scripts or add patterns to `.gitignore` | No | No |
| R-005 | Healthy path for `/health` not live integration-tested yet | Medium | OPEN | 2026-05-15 | 2026-05-15 | UNCHANGED | Re-run local `/health` and `/ready` with reachable DB; verify **200** after operational-health deploy | No | No |

---

## ACCEPTED / DEFERRED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Changed Since Last Report | Next Action | Blocks Merge? | Blocks Deploy? |
|----|----------------|----------|--------|------------|--------------|---------------------------|-------------|---------------|----------------|
| R-004 | No formal PR record for council-synthesis merge | Low | ACCEPTED | 2026-05-15 | 2026-05-15 | UNCHANGED | Use PRs for all future merges | No | No |
| R-006 | No Engineering OS automation yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | Build after health layer and hardening (T-104) | No | No |
| R-007 | No Dynamic Provider Config yet | Medium | DEFERRED | 2026-05-15 | 2026-05-15 | UNCHANGED | Build after operational discipline foundation (T-106) | No | No |

---

## FIXED

| ID | Risk / Issue | Severity | Status | First Seen | Last Checked | Resolved | Notes |
|----|----------------|----------|--------|------------|--------------|----------|-------|
| — | *(none yet)* | — | — | — | — | — | Move closed risks here; do not delete rows |

---

## Severity guide

| Level | Meaning |
|-------|---------|
| Low | Annoyance or process gap; workaround exists |
| Low–Medium | May affect ops confidence or repeat debugging |
| Medium | Affects observability, deploy confidence, or feature quality |
| High | Data loss, security, or production outage risk |

---

## Status values

| Status | Meaning |
|--------|---------|
| OPEN | Not started |
| IN_PROGRESS | Mitigation underway |
| ACCEPTED | Known; consciously not fixing now |
| DEFERRED | Planned later; tracked in TASK_QUEUE |
| FIXED | Mitigated; row moved to FIXED section |

---

READY FOR CHATGPT REVIEW
