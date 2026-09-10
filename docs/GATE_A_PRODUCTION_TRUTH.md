# Gate A — Production Truth (read-only)

**Observed:** 2026-09-10  
**Deploy SHA:** `8c2bbd18aa4e7c3fb448da09643c05665ed54bd0` (matches `/health` `version` and `origin/main`)  
**API:** `https://ben-v2-production.up.railway.app`  
**Method:** Railway project token (read-only CLI + public HTTP). No Railway writes, deploys, variable changes, volume mutations, SSH key adds, or POST drain from this agent.

**Status: GATE A INCOMPLETE.** Production flags and idle runner cadence are observed. Queue gauges and Path A vs B fingerprint still require additional secrets. **Do not start Gate B.**

Secrets are reported as PRESENT / ABSENT / ON / OFF / UNSET only. Values are not printed.

---

## 0. Access check (this Cloud Agent environment)

| Secret in agent env | State |
|---------------------|--------|
| `RAILWAY_TOKEN` | **PRESENT** (project-scoped; `railway whoami` → Unauthorized, `railway status` works) |
| `BEN_DOC_PROCESSING_CRON_SECRET` | **ABSENT** |
| `BEN_GATE_A_CANARY_BEARER` / `CANARY_BEARER` | **ABSENT** |

Railway project: **empowering-quietude** · environment **production**.  
Services: **ben-v2** Online; **Postgres** Online; cron **curl** and **ben-doc-initial-read-cron** (`*/5 * * * *`).

`railway setup agent` was **not** run (would modify local/editor config). No SSH keys were added (volume file listing requires keys; adding them is a write).

---

## 1. What is now observable with the Railway token

### 1.1 Live HTTP (unsigned)

| Probe | Result |
|-------|--------|
| `GET /health` | **200** `status=healthy`, `version=8c2bbd18…`, `checks.database=ok`, `enforce_auth=false`, `clerk_secret_configured=true`. Health does **not** expose file-processing flags. |
| `GET /ready` | **200** `ready=true`, `migration_head=031_workspace_file_evidence_ir` |
| `GET /api/internal/documents/processing/runner/stats` (no cron header) | **401** `Invalid or missing document-processing cron secret` — secret **is configured** in the running app (503 would mean unset) |
| `GET /api/workspaces/{uuid}/files` unsigned | **401** Unauthorized (Gate A) |
| `GET` runner drain URL | **405** Method Not Allowed (POST-only; this agent did not POST) |

### 1.2 ben-v2 production variables (values not printed)

| Flag / key | Production |
|------------|------------|
| `BEN_DOC_PROCESSING_ENABLED` | **ON** |
| `BEN_DOC_RUNNER_ENABLED` | **ON** |
| `BEN_DOC_UPLOAD_WAKE_ENABLED` | **ON** |
| `BEN_DOC_RUNNER_CLAIM_GLOBAL` | **ABSENT** (code ignores it; claim path is persisted `runner_eligible`) |
| `BEN_REQUIRE_DURABLE_FILE_ROOT` | **ON** |
| `BEN_PROJECTS_DATA_DIR` | **PRESENT** `/data/projects` |
| `BEN_DURABLE_FILE_MOUNT` | **ABSENT** |
| `RAILWAY_VOLUME_MOUNT_PATH` | **PRESENT** `/data` |
| `BEN_WORKSPACE_CHUNK_RETRIEVAL` | **ABSENT** (Gate 4A fail-closed **OFF**) |
| `BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS` | **ABSENT** |
| `BEN_DOC_PROCESSING_CRON_SECRET` | **PRESENT** on the Railway service (not in this agent) |
| `BEN_DOC_RUNNER_FILE_IDS` | **PRESENT**, count=1 (env allowlist exists; claim path is persisted eligibility, not this list) |
| `BEN_DOC_RUNNER_WORKSPACE_IDS` | **ABSENT** |
| `BEN_DOC_UPLOAD_WAKE_CONCURRENCY` | **ABSENT** (code default 2) |
| `BEN_DOC_EVIDENCE_IR_WRITE` | **ABSENT** (IR writes OFF) |
| `ENFORCE_AUTH` | **ABSENT** (matches health `enforce_auth=false`) |
| `DATABASE_URL`, Clerk secret, OpenAI/Anthropic/Google/xAI, Stripe | **PRESENT** |
| `CLERK_PUBLISHABLE_KEY` | **ABSENT** on this service (frontend likely elsewhere) |
| `MISTRAL_API_KEY` | **ABSENT** on this service |

### 1.3 Deploy, volume, region

- Latest **ben-v2** deploy: `6acf171e-…`, **SUCCESS**, created 2026-09-07, branch `main`, commit `8c2bbd18…` (PR #53). Volume mounts: `/data`. 1 replica, region **sfo**. Python 3.11 via Railpack.
- Volume **ben-v2-volume**: mount `/data`, ~833–853 MB used / 50 GB, Ready.
- Volume **postgres-volume**: `/var/lib/postgresql/data`, ~1.17 GB / 50 GB, Ready.
- Metrics (last 6h): CPU ~0.001 vCPU, memory ~113 MB, HTTP 151 2xx / 7 4xx / 0 5xx, volume ~853 MB.

### 1.4 Cron shape (commands redacted)

| Service | Schedule | Start command (secret redacted) |
|---------|----------|-----------------------------------|
| `curl` | `*/5 * * * *` | `POST …/api/internal/documents/processing/runner/drain?limit=1` with `X-BEN-Doc-Processing-Cron-Secret` |
| `ben-doc-initial-read-cron` | `*/5 * * * *` | `POST …/api/internal/documents/processing/initial-read/drain?limit=5` with the same header |

There is **no** cron for generic `POST /api/internal/documents/processing/drain`.

Both cron containers log `Starting Container` then silence. `curl -fsS` is quiet on HTTP success, so this is consistent with authenticated drain returning 2xx. This agent did **not** invoke those POSTs.

### 1.5 Application logs (ben-v2, last 250 lines, ~14:31–20:38 UTC)

| Signal | Count in window |
|--------|----------------|
| `subsystem=doc_processing` `operation=runner_drain` `outcome=no_eligible_job` | **73** (~every 5 minutes) |
| `upload_wake` | **0** |
| `document_processing_timing` | **0** |
| error-level lines | **0** in this sample |

`no_eligible_job` is only logged after the runner claim policy is **eligible** (disabled runner would log `outcome=disabled` and return). Idle eligible queue, not a canary.

Cron service logs (last 40 each): start/stop only; no curl error bodies.

### 1.6 Volume file listing

**Not observed.** `railway volume files list / -v ben-v2-volume` → `No SSH keys found.` Adding Railway SSH keys would be a write. Stopped.

---

## 2. What still requires extra secrets

### 2.1 `BEN_DOC_PROCESSING_CRON_SECRET` (inject into this agent env; do not paste in chat)

Read-only **GET** (do not POST drain):

`GET /api/internal/documents/processing/runner/stats`

Expected gauges (no document text): `claim_policy`, `runner_enabled`, `due_queue_depth`, `oldest_due_queued_age_s`, `running_count`, `failed_count`, `retry_count`, `succeeded_24h`, plus configured file/workspace id lists.

Without this, queue depth / failed / retry / 24h success remain **UNKNOWN**. Idle `no_eligible_job` logs are not a substitute for those gauges.

### 2.2 `BEN_GATE_A_CANARY_BEARER` (Clerk Bearer for a throwaway workspace)

Required to fingerprint **PRODUCTION PATH A vs B** on a real upload:

1. Tiny `canary-gate-a.txt` into a disposable workspace.
2. Poll `GET` file until terminal.
3. Record: `status`, `extraction_status`, `index_status`, pages/chunks present, job row vs sync `process_file`.
4. **DELETE** the canary file.

Flags + durable `/data` + runner cron + wake ON + `no_eligible_job` cadence are **strongly consistent with Path B**, but the runbook requires a canary before calling the path proven.

Unsigned files API cannot do this (401).

---

## 3. Path inference (not a Gate A pass)

| Question | Answer |
|---------|--------|
| Structured processing flags on? | **YES** (`BEN_DOC_PROCESSING_ENABLED`, runner, upload-wake all ON) |
| Durable bytes root? | **YES** (`BEN_REQUIRE_DURABLE_FILE_ROOT=ON`, `BEN_PROJECTS_DATA_DIR=/data/projects`, volume `/data`) |
| Gate 4A chunk FTS? | **OFF** (flag unset, no workspace allowlist) |
| Evidence IR writes? | **OFF** |
| Runner claiming? | **Eligible-only**, idle (`no_eligible_job`) |
| **PRODUCTION PATH** | **INFERRED B, NOT FINGERPRINTED** |
| Gate B (retry → structured) priority | **UNDETERMINED** until canary + stats |

---

## 4. Explicit stops

- No `railway variable set`, redeploy, restart, volume upload/delete/rename, SSH key add, or `railway setup agent`.
- No POST to drain/wake/initial-read from this agent.
- No production DB writes; no `DATABASE_URL` dumps.
- No Gate B implementation.

**Next:** inject cron secret (stats GET) and optional canary bearer; then finish the A packet. Until then Gate A stays incomplete.
