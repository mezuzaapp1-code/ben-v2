# BEN-V2 Production File Infrastructure Audit

**Date:** 2026-09-10  
**Baseline:** `main` @ `8c2bbd1` (PR #53, flag-gated WRAP evidence IR sidecar)  
**Method:** Repository-wide inspection of the current tree. No prior Cursor chat conclusions were reused.  
**Primary subject:** existing production File Library (`WorkspaceFile` / `services.workspace_files`).  
**Secondary:** adjacent file-like stores, assessed only where they can leak into or duplicate File Library behavior.

---

## 1. Scope and findings class

| Item | Status |
|------|--------|
| `prototypes/ben_quote/` | **Absent** from this repository (no `prototypes/` directory, no `ben_quote` string) |
| Production File Library | Present, gated, and test-covered on `main` |
| Production Railway env values | **INFERRED** — not read in this session |
| Live production upload/drain smoke | **NOT VERIFIED** |

**VERIFIED** means the statement is grounded in current files, tests, or migrations.  
**INFERRED** means production runtime (Railway flags, volume mount, cron schedule) was not observed.

---

## 2. Executive summary

BEN-V2 already has a **tenant-scoped File Library** that is distinct from News and from the older SQLite knowledge-upload path. It is not a prototype: it has Postgres tables, FORCE RLS, authenticated HTTP APIs, a durable job ledger, structured extraction, chat injection, and a File Library UI.

The as-built system is a **layered gate stack** (V1 library → Gates 1–4A → upload intelligence → Initial Read) with **fail-closed flags**. Default code paths still favor the **legacy synchronous extractor** unless `BEN_DOC_PROCESSING_ENABLED` is on. Chunk FTS (Gate 4A) is also fail-closed OFF and still requires a workspace UUID allowlist.

The largest structural facts:

1. **Canonical store for user uploads is `ben.workspace_files`**, not News `SourceDocumentVersion` and not `KnowledgeObject`.
2. **Two extraction implementations coexist.** Sync retry/upload-OFF uses `extract.py`. Async drain uses `run_structured_extraction` and then **projects** legacy `status` + `extracted_text` so Gate 3D chat still works.
3. **Standard streaming chat is the only path that injects File Library text.** Council / Add Opinion (`expert_opinion=True`) and non-stream `handle_chat` skip it. Current-turn Vision skips it and reads raw bytes instead.
4. **A second live upload path still exists:** project knowledge files in per-project SQLite (`services/knowledge_store.py`, 500 MB cap). That path does not become a `WorkspaceFile`.
5. **Normative ops docs are stale.** `docs/BEN_SYSTEM_MAP.md`, `docs/DATA_GOVERNANCE.md`, `docs/PROJECT_STATE.md`, and `docs/SECURITY_BASELINE.md` still describe a world without File Library (and, in places, without Gate A).

---

## 3. Architecture as built

```text
Browser (Clerk JWT)
  → POST/GET/DELETE /api/workspaces/{id}/files   (alias: /api/projects/{id}/files)
  → routers/workspace_files.py  (_require_files_tenant = Gate A)
  → services/workspace_files/service.py
       upload → local FS _workspace_files/{org}/{workspace}/{file}/…
       persist WorkspaceFile (RLS org_id)
       if BEN_DOC_PROCESSING_ENABLED:
            enqueue document_processing_jobs (atomic with file row)
            optional post-commit wake (scoped file_id drain)
       else:
            process_file() → extract.extract_text → status ready/failed

Cron (X-BEN-Doc-Processing-Cron-Secret)
  → /api/internal/documents/processing/{drain|runner/drain|files/{id}/drain}
  → drain.py → run_structured_extraction
       pages + chunks + lifecycle + legacy extracted_text projection
  → /api/internal/documents/processing/initial-read/drain
  → initial_read.py (LLM overview; separate from extraction)

Standard POST /chat/stream (project_id set, not vision, not expert_opinion)
  → thread source_state (pending/active/recent/burst)
  → load_ready_files_context
       Gate 4A FTS if flag+allowlist else Gate 3D ranked prefixes of extracted_text
  → prefix <workspace_files>… onto user message
  persist used_files + response_evidence on assistant envelope
```

**Workspace == Project** in the product model (`WorkspaceFile.workspace_id` FK → `ben.projects.id`; `project_id` is a duplicate column set to the same UUID on upload).

---

## 4. Canonical File Library inventory

### 4.1 Owned code

| Area | Path |
|------|------|
| HTTP (customer) | `routers/workspace_files.py` — `/api/workspaces/{id}/files` and `/api/projects/{id}/files` alias |
| HTTP (system) | `routers/document_processing.py` — cron-secret drain/stats |
| Domain | `services/workspace_files/` (25 modules) |
| Models | `database/models.py` — `WorkspaceFile`, `WorkspaceFilePage`, `WorkspaceFileChunk`, `WorkspaceFileEvidenceIR`, `DocumentProcessingJob` |
| Auth | `auth/persistent_access.py` (Gate A), `auth/doc_processing_cron_auth.py` |
| Frontend | `frontend/src/api/workspaceFiles.js`, `FileLibraryOverlay.jsx`, `FileLifecycleStatus.jsx`, `lib/fileStatus.js`, `lib/workspaceFileInventory.js` |

Hard domain wall: `services/workspace_files/domain_boundary.py`. File Library must not import News services/routers or persist into News tables. Tests: `tests/test_news_files_domain_separation.py`.

### 4.2 Postgres schema (migrations 022–031)

| Migration | What it added |
|----------|-------------|
| `022_workspace_files_v1` | `ben.workspace_files` + FORCE RLS |
| `023_document_intelligence_foundation` | lifecycle columns; `workspace_file_pages`; `workspace_file_chunks` + GIN `text_tsv` |
| `024_document_processing_jobs` | job ledger; `ben_doc_processor` role; SECURITY DEFINER claim/reap |
| `025_claim_document_processing_job_for_file` | file-id-scoped claim |
| `026_claim_jobs_for_allowlist` | allowlist claim helpers |
| `027_runner_eligible_jobs` | `runner_eligible`; historical quarantine trigger |
| `029_document_upload_intelligence_v1` | thread `source_state` + related |
| `030_file_initial_read_jobs` | `file_initial_read` job type + `initial_read_status` |
| `031_workspace_file_evidence_ir` | optional WRAP IR sidecar table |

`028_projects_org_updated_id` is a Project index, not File Library.

### 4.3 `WorkspaceFile` lifecycle fields (VERIFIED)

Upload/bytes: `status ∈ {uploaded, queued, processing, ready, failed}`.

Independent of `status`:

- `extraction_status ∈ {pending, extracting, complete, partial, failed}`
- `index_status ∈ {not_indexed, indexing, indexed, stale, failed}`
- `initial_read_status ∈ {none, pending, complete, failed, skipped}`

UI `processing_stage` (`lifecycle.py`) is **derived**. READY is fail-closed on `status == ready`. Extracting/Indexing are shown only while the durable job is `running`.

### 4.4 Bytes on disk (VERIFIED)

- Root: `projects_root() / "_workspace_files"` (`storage.py`).
- Default root: `<repo>/data/projects` unless `BEN_PROJECTS_DATA_DIR` is set (`project_tools.projects_root`).
- Key: `{org_id}/{workspace_id}/{file_id}/{ascii_sanitized_name}`.
- Display names keep Unicode; disk names are ASCII-sanitized (`preserve_original_filename` vs `sanitize_filename`).
- Cap: **50 MB**. Stream + fsync + size/checksum verify. Empty uploads rejected.
- Dangerous extensions rejected (`.exe`, `.js`, `.sh`, …) even if MIME is spoofed (`types.py`).
- Supported: PDF, DOCX, TXT/MD, CSV, XLSX, PPTX (store-only), common images, JSON. `.doc` store-only.

When `BEN_REQUIRE_DURABLE_FILE_ROOT` is on: missing/unwritable root → `DurableStorageUnavailable` → HTTP **503**. Default OFF for local/tests.

**Not present:** object storage (S3), virus scan, encryption-at-rest beyond host volume, retention/purge job.

---

## 5. Auth, tenancy, and processing identity

### 5.1 Customer File Library (Gate A) — VERIFIED

`_require_files_tenant` does **not** honor `ENFORCE_AUTH=false`. Unsigned → **401**. Shared anonymous org is rejected. Allowed identities: Clerk personal/org JWT, or isolated beta passcode context.

The same Gate A now wraps chat/council/threads (`test_security_gate_a.py`). Product News remains on shadow/`ENFORCE_AUTH` and can still be anonymous when enforce is off.

### 5.2 RLS — VERIFIED

`workspace_files`, pages, chunks, jobs, and evidence IR: `ENABLE` + `FORCE ROW LEVEL SECURITY`, `org_id = app.current_org_id`. Product sessions call `set_config('app.current_org_id', …)` before reads/writes and also filter `org_id` + `workspace_id` in SQLAlchemy.

Jobs add a composite FK `(file_id, org_id, workspace_id) → workspace_files` so a job cannot be retargeted across tenants. CASCADE on file delete.

### 5.3 System drain — VERIFIED

`/api/internal/documents/*` uses `BEN_DOC_PROCESSING_CRON_SECRET` via header `X-BEN-Doc-Processing-Cron-Secret`. Missing secret → **503** (disabled). Wrong secret → **401**. HMAC compare. Not a Clerk route.

Cross-org claim/reap/complete run as SECURITY DEFINER functions owned by **NOLOGIN** role `ben_doc_processor` (migration 024). Product sessions stay org-isolated.

`Procfile` is a **single web process** (`uvicorn main:app`). There is no in-repo worker process. Drain is HTTP-triggered (cron / scoped wake).

---

## 6. Dual extraction and job orchestration

This is the most important internal split.

### 6.1 Path A — legacy `process_file` / `extract.py`

Runs when:

- `BEN_DOC_PROCESSING_ENABLED` is **OFF** (code default) on upload, **or**
- customer **retry** (`POST .../files/{id}/retry`) — always calls `process_file`, regardless of the async flag.

Behavior: set `queued` → `processing` → `extract_text` → write **only** `extracted_text` + `status`. No pages, no chunks, no `extraction_status` / `index_status` updates. PDF via pypdf, max **80 pages**, **200k** chars. Images and PPTX succeed with empty text.

### 6.2 Path B — structured pipeline (drain)

`drain.py` executors:

| `job_type` | Executor |
|------------|----------|
| `file_extraction` | `run_structured_extraction` |
| `structured_extraction` | `run_structured_extraction` |
| `file_initial_read` | **not executed** — requeued `wrong_drain` |

So comments that say Gate 3B waits for `process_file` are **stale**. Async workers run the Gate 2 structured pipeline.

Structured pipeline (`extraction_pipeline.py`):

- Parses via `document_parser.py` (PdfDocumentParser / GenericTextParser / ImagePlaceholderParser).
- Persists one `WorkspaceFilePage` per detected page and versioned `WorkspaceFileChunk` rows (FTS generated column).
- **Legacy projection:** copies concatenated page text into `WorkspaceFile.extracted_text` and sets `status=ready` so Gate 3D chat still works. Comment still says this bridge is “removed when Gate 4 retrieval lands”; Gate 4A exists but is flag-gated, so the projection is still load-bearing.
- Image / needs_ocr-only sources: **`ready` with empty text**, not `failed` (`valid_source_without_text`).
- **No OCR** in this stack. `needs_ocr` is a coverage flag for a future gate.
- Evidence IR writes only if `BEN_DOC_EVIDENCE_IR_WRITE` is ON; current parsers emit no IR.

Stale docstring: `extraction_pipeline.py` still says it is “NOT auto-wired into the upload critical path in Gate 2”. Drain **does** wire it when the processing flag is on.

### 6.3 Upload → job (when flag ON)

`upload_file` inserts `WorkspaceFile` (`status=queued`) and enqueues a `file_extraction` job **in the same transaction**. Enqueue failure rolls back the file row (no orphan). New jobs are `runner_eligible=true` unless the file id is in the hard quarantine.

Quarantined production file ids (Python + SQL trigger):

- `43cef794-1fff-40ae-bd3c-47d9fc121518`
- `0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4`

These must never be claimed, reaped, or marked eligible.

### 6.4 How jobs actually run

| Trigger | Endpoint / function | Claim policy |
|---------|---------------------|--------------|
| Generic cron | `POST /processing/drain` | bounded FIFO of queued extraction jobs |
| File-scoped | `POST /processing/files/{file_id}/drain` | that file only; no FIFO fallback |
| Runner cron | `POST /processing/runner/drain` | `runner_eligible` only; `CLAIM_GLOBAL` is **ignored** |
| Upload wake | `schedule_upload_wake` → scoped drain | same file id; requires processing + runner + wake flags; concurrency default 2 |

Wake is best-effort and **must not** fail the upload HTTP. Cron is the recovery path. Per-job timeout default **120s**; max attempts **5**; lease default **300s**.

### 6.5 Initial Read (post-READY)

After a file reaches READY, `notify_file_processed` may enqueue `file_initial_read`. That job is drained only on `/processing/initial-read/drain` and **does** call an LLM. Extraction drain never runs it. Skipped for vision/image media.

---

## 7. How files enter conversation

### 7.1 Standard streaming chat only — VERIFIED

`services/chat_service.py` `stream_chat_response`:

- Requires `project_id` and `vision_user_content is None`.
- Skipped when `expert_opinion=True` (council / Add Opinion rolling context).
- Non-stream `handle_chat` does **not** call `load_ready_files_context`.

Budget: `BEN_WORKSPACE_FILES_CONTEXT_MAX_CHARS` default **12000**. Gate 3D per-file: `BEN_WORKSPACE_FILES_PER_FILE_MAX_CHARS` default **2000**.

Eligibility for text injection: `status == ready` **and** non-empty `extracted_text`. Images with no text never enter Gate 3D.

### 7.2 Gate 3D vs Gate 4A

| Path | Enablement | Mechanism |
|------|------------|-----------|
| Gate 3D | default (chunk flag off) | Rank READY files by lexical/filename score, **then** clip to budget. Selection precedes budget (`file_resolver.py`). |
| Gate 4A | `BEN_WORKSPACE_CHUNK_RETRIEVAL=on` **and** workspace UUID in `BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS` | Postgres FTS on `workspace_file_chunks.text_tsv`. Empty allowlist enables **no** workspace. |
| 4A fallback | no tokens / not indexed / FTS error / no match | Named-file prefixes only — **does not dump all READY files** |

Gate 4A caps (hardcoded): 40 considered, 8 selected, 4/file, 6000 evidence chars, 3000/file, FTS timeout **0.2s**.

**Contradiction (VERIFIED):** Gate 3D context object uses `retrieval_mode="off"` / `fallback_reason="flag_off"`, while `build_response_evidence` stores `retrieval_mode="prefix_fallback"` on the envelope.

### 7.3 Thread source state (upload intelligence)

`Thread.source_state` tracks `conversation_file_ids`, `pending_file_ids`, `active_file_ids`, `recent_file_ids`, `burst_file_ids` (`thread_sources.py`, `source_policy.py`).

Policy: 2 unused turns before active→recent; 20-minute idle TTL; 120s burst window.

`resolve_turn_sources` can restrict retrieval, ask a clarification, or cover a multi-file set. Load errors fail closed to an empty allow-list (never dump the library).

Draft chats must persist a server thread before attach (`source_chat_id`); PR #50.

### 7.4 Used files / unavailable / Sources

On the assistant envelope and NDJSON `done`:

- `used_files`: `{id, name}` of files **actually injected**
- `unavailable_count`: workspace files still `uploaded|queued|processing`
- `response_evidence`: injected-only excerpts (`origin: ben_retrieval`)

UI: Used files list + Sources panel. Backend status is source of truth (`fileStatus.js`). No invented percentages.

### 7.5 Current-turn Vision (adjacent, not retrieval)

Composer image `file_ref` → `open_file_bytes` → multimodal provider parts. Does **not** wait for READY/OCR/index. If vision loads, Gate 3D/4A is skipped this turn. Vision uploads are not recorded as pending/active sources. Opinion mode forbids vision.

### 7.6 Large Paste (not a WorkspaceFile)

Conversation-scoped `large_paste` parts. Provider sees full body; retrieval/focus query uses a stub. No library upload.

---

## 8. Adjacent file-like systems (assessed separately)

These are **not** File Library. They matter because operators can confuse them with “files in BEN”.

| Store | API / module | Chat effect | Live? |
|-------|--------------|-------------|-------|
| **WorkspaceFile** | `/api/workspaces/{id}/files` | Gate 3D/4A + Vision bytes | **Yes — production File Library** |
| **Project knowledge files** | `POST /api/projects/{slug}/knowledge/upload-stream` → SQLite `knowledge_store` + disk under `projects_root()/{slug}/` | Active Attention / project agent heads — **not** `load_ready_files_context` | Yes; **500 MB** cap; parallel store |
| **SQLite knowledge bases** | `/api/knowledge/bases` + documents | Few-shot inject if the message names a base | Yes; text docs, not uploads-as-files |
| **Postgres `KnowledgeObject`** | `database.models.KnowledgeObject` | Project memory types; not file bytes | Live for memory, not File Library |
| **News `SourceDocumentVersion`** | Forbidden in `domain_boundary.py` | Must never appear in File Library | No model in current `models.py` |
| **Project tool files** | `services/project_tools.py` `data/projects/{slug}/specs\|tasks` | Agent onboarding tools | Yes, local FS |
| **Camera / receipt** | `CameraCaptureInput` + invoice tools | Ledger Action Cards; not WorkspaceFile ingest | Yes |
| **Evidence IR sidecar** | `workspace_file_evidence_ir` | Not consulted by chat/Sources | Table exists; writes flag-gated OFF |

**Risk:** two user-visible “upload a file” stories (File Library vs project knowledge stream) with different auth, size caps, storage, and chat wiring.

---

## 9. `prototypes/ben_quote/`

**Not in BEN-V2.** Search of the tree: no `prototypes/` directory, no `ben_quote` identifier.

If that prototype lives in another repository or an unpushed tree, it is **out of band** for this audit. It does not implement or replace `ben.workspace_files`. Do not treat quote-prototype storage, parsers, or retrieval as BEN-V2 production file infrastructure.

---

## 10. Feature flags

Almost all processing/retrieval flags are **fail-closed OFF**.

| Flag | Default | Role | In `.env.example`? |
|------|---------|------|--------------------|
| `BEN_DOC_PROCESSING_ENABLED` | OFF | Master async job path | **No** |
| `BEN_DOC_PROCESSING_CRON_SECRET` | unset → drain **503** | Drain auth | **No** |
| `BEN_DOC_RUNNER_ENABLED` | OFF | Claim `runner_eligible` jobs | Yes (commented) |
| `BEN_DOC_RUNNER_CLAIM_GLOBAL` | ignored | Must not open generic FIFO | Yes (commented) |
| `BEN_DOC_UPLOAD_WAKE_ENABLED` | OFF | Post-commit scoped wake | Yes (commented) |
| `BEN_DOC_UPLOAD_WAKE_CONCURRENCY` | 2 | Wake slots | Yes |
| `BEN_REQUIRE_DURABLE_FILE_ROOT` | OFF | Fail-closed volume | **No** |
| `BEN_PROJECTS_DATA_DIR` | `<repo>/data/projects` | Bytes root | **No** |
| `BEN_DURABLE_FILE_MOUNT` / `RAILWAY_VOLUME_MOUNT_PATH` | unset | Mount constraint | **No** |
| `BEN_WORKSPACE_CHUNK_RETRIEVAL` | OFF | Gate 4A master | Yes |
| `BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS` | empty = none | 4A allowlist | Yes |
| `BEN_WORKSPACE_FILES_CONTEXT_MAX_CHARS` | 12000 | Chat budget | Yes |
| `BEN_WORKSPACE_FILES_PER_FILE_MAX_CHARS` | 2000 | Gate 3D per-file | Yes |
| `BEN_DOC_EVIDENCE_IR_WRITE` | OFF | IR sidecar writes | **No** |
| `BEN_DOC_DRAIN_LIMIT` | 5 | Drain batch | **No** |
| `BEN_DOC_JOB_TIMEOUT_S` | 120 | Per-job | **No** |
| `BEN_DOC_JOB_MAX_ATTEMPTS` / lease / retry | 5 / 300s / 30s–3600s | Job policy | **No** |
| `BEN_MAX_EXTRACT_PAGES` | 1000 | Structured parser cap | **No** |
| `BEN_DOC_CHUNK_MAX_CHARS` | 1500 | Chunk size | **No** |
| `BEN_WORKER_ID` | `web-{hex}` | Drain worker id | **No** |

**INFERRED:** which of these are set on Railway. Code defaults imply that **unless production explicitly turned flags on**, uploads still process **synchronously inside the web request** via Path A.

---

## 11. Frontend (File Library)

| Surface | Behavior |
|---------|----------|
| Composer `+` | Attach → `source_chat_id`; draft thread is persisted first |
| In-flow row | `kind: file_upload` / `FileLifecycleBubble` — one conversation row, not a separate queue widget |
| File Library overlay | List/search/preview/download/retry/delete |
| Sidebar | Lifecycle chips; hide raw queue jargon from users |
| Polling | 3s while non-terminal; no WebSocket |
| Auth | `acquirePersistentHeaders` waits for a real identity; unsigned persistent calls are skipped |

Honesty rules: never show READY unless backend `status` is ready; no fake percent complete; image-only READY is a stored image, not a failed extract.

---

## 12. Test coverage (present on `main`)

Backend (selected):

- `test_workspace_files_v1.py`, `test_workspace_files_durable_storage.py`, `test_unicode_filename_preserve.py`
- `test_security_gate_a.py`
- `test_document_intelligence_gate1.py` … `gate4a.py`
- `test_document_intelligence_gate3b.py` / `3c.py` (flag ON vs OFF; drain → structured, not `process_file`)
- `test_document_processing_runner.py`, `test_document_processing_scoped_drain.py`, `test_document_upload_wake.py`
- `test_files_auto_ingest_eligibility.py`, `test_image_only_lifecycle.py`, `test_file_lifecycle_stage.py`
- `test_workspace_files_chat_context.py`, `test_files_used_files_durability.py`, `test_response_evidence_v1.py`
- `test_document_upload_intelligence_v1.py`, `test_multi_source_resolution_v1.py`
- `test_current_turn_vision.py`, `test_news_files_domain_separation.py`, `test_evidence_ir_sidecar.py`

Frontend node scripts: `test-files-ui.mjs`, `test-files-status-honesty.mjs`, `test-files-lifecycle-ux-truth.mjs`, `test-file-lifecycle-inflow.mjs`, `test-response-evidence-v1.mjs`.

`scripts/validate_workspace_files_v1.py` is a live DB validation helper (local `.env`), not CI.

---

## 13. Docs vs code drift

| Doc | Last updated | File Library? | Other drift |
|-----|----------------|----------------|-------------|
| `docs/BEN_SYSTEM_MAP.md` | 2026-06-06 | **No** | Persistence diagram is threads/messages/KOs only |
| `docs/DATA_GOVERNANCE.md` | v1 | **No** | No `workspace_files`, pages, chunks, jobs, or `_workspace_files` FS; no file retention |
| `docs/SYSTEM_BOUNDARIES.md` | 2026-06-06 | **No** | Boundary lives in code (`domain_boundary.py`) |
| `docs/PROJECT_STATE.md` | 2026-06-06 | **No** | Still says unsigned chat/council allowed — **stale vs Gate A** |
| `docs/SECURITY_BASELINE.md` | foundation v1 | **No** | Still classifies `/chat` auth as **None** |
| `docs/CURRENT_PHASE.md` / `ROADMAP.md` | 2026-05-19 | **No** | Still “provider-first chat stabilization”; File Library shipped later as gated PRs |
| `.env.example` | current-ish | Partial | Missing master processing flag, cron secret, durable root |
| Gate comments in `job_queue.py` / `extraction_pipeline.py` | in-tree | Partial | Still describe Gate 3A/3B as unwired or as `process_file` |

File contracts are currently enforced by **tests + module comments**, not by the normative system map.

---

## 14. Risks and sharp edges

Severity here is engineering/ops, not the historical R-ID register (which also does not mention files).

| ID | Severity | Finding | Class |
|----|----------|---------|-------|
| F-01 | High | Dual extractors: retry always uses Path A (`extract.py`) even when async Path B produced pages/chunks. Retry can READY a file without re-indexing. | VERIFIED in code |
| F-02 | High | Chat text injection still requires `extracted_text`. Gate 4A is allowlisted. If production processing is ON but 4A is OFF, chat depends on the legacy projection. | VERIFIED |
| F-03 | High | Bytes live on local FS / Railway volume. Without `BEN_REQUIRE_DURABLE_FILE_ROOT`, a mis-set `BEN_PROJECTS_DATA_DIR` can write ephemeral disk. Volume + flags **INFERRED** in prod. | VERIFIED code / INFERRED prod |
| F-04 | High | Parallel knowledge upload (SQLite, 500 MB) vs File Library (Postgres, 50 MB) — two “files” products. | VERIFIED |
| F-05 | Medium | Drain is cron/HTTP on the web dyno. No worker `Procfile` entry. If cron/wake/flags are off, async uploads stay `queued` forever. | VERIFIED code / INFERRED prod schedule |
| F-06 | Medium | Council and non-stream chat never see File Library text. Users can reasonably expect “files in this project” to apply to council. | VERIFIED |
| F-07 | Medium | No OCR. Scanned PDFs/images are READY with empty text; Gate 3D will not inject them; Vision can still send pixels for current-turn images. | VERIFIED |
| F-08 | Medium | Hard-coded quarantine UUIDs in Python + SQL. Operational debt if those rows are deleted or cloned. | VERIFIED |
| F-09 | Medium | Evidence `retrieval_mode` vs diagnostics disagree on the Gate 3D path (`off` vs `prefix_fallback`). | VERIFIED |
| F-10 | Medium | No retention, malware scan, or object-storage failover. Aligns with general “no purge” data policy, but file bytes are larger/stickier than chat text. | VERIFIED |
| F-11 | Medium | SECURITY DEFINER + cron secret concentrate cross-org processing power. Rotation/ops are not in `SECRETS_GOVERNANCE.md`. | VERIFIED code / INFERRED ops |
| F-12 | Low | `BEN_DOC_RUNNER_FILE_IDS` / `WORKSPACE_IDS` are still parsed but are **not** the claim path; persisted `runner_eligible` is. | VERIFIED |
| F-13 | Low | `extraction_pipeline` / `job_queue` module docs lag the drain wiring. | VERIFIED |
| F-14 | Docs | System map, data governance, security baseline, project state omit File Library and (for auth) contradict Gate A. | VERIFIED |

---

## 15. What “production file infrastructure” is *not*

- Not a vector/embedding index (Gate 4A is lexical Postgres FTS).
- Not OCR / document understanding beyond pypdf + XML unzip of DOCX/XLSX.
- Not News ingestion.
- Not the quote prototype (`prototypes/ben_quote` is not here).
- Not multi-replica-safe job execution beyond SKIP LOCKED + leases (web process + cron). Idempotency for chat remains in-process elsewhere.
- Not a replacement for thread transcript as conversation truth.

---

## 16. Recommended next steps (order)

These are recommendations from this audit, not implemented in this change.

1. **Operator truth:** Document (or confirm) production values of `BEN_DOC_PROCESSING_ENABLED`, runner/wake, cron secret presence, `BEN_REQUIRE_DURABLE_FILE_ROOT`, volume mount, and Gate 4A allowlist. Until that is confirmed, treat async structured extraction as **possibly off** in production.
2. **Unify retry with drain.** `POST .../retry` should enqueue/re-run structured extraction, not `process_file`, when the durable path is the production processor.
3. **Pick one upload product for “files in a project.”** Either fold knowledge-stream uploads into `WorkspaceFile`, or keep them but document and UI-separate them so they cannot be mistaken for File Library.
4. **Update normative docs:** `SYSTEM_BOUNDARIES.md` (File Library layer), `DATA_GOVERNANCE.md` (tables + FS + retention), `BEN_SYSTEM_MAP.md` (request lifecycle), `SECURITY_BASELINE.md` / `PROJECT_STATE.md` (Gate A).
5. **Add missing `.env.example` entries** for the master processing flag, cron secret (name only), and durable root.
6. **Do not start from `prototypes/ben_quote`.** It is not in this repo; production File Library is already the subject.

---

## 17. Key file index (as-built)

| Concern | Paths |
|---------|--------|
| Upload/list/get/delete/retry | `routers/workspace_files.py`, `services/workspace_files/service.py` |
| Bytes | `services/workspace_files/storage.py`, `types.py` |
| Legacy extract | `services/workspace_files/extract.py` |
| Structured extract | `document_parser.py`, `chunking.py`, `extraction_pipeline.py` |
| Jobs | `job_queue.py`, `drain.py`, `runner_config.py`, `ingest_eligibility.py`, `upload_wake.py` |
| Chat injection | `service.load_ready_files_context`, `file_resolver.py`, `chunk_retriever.py`, `chat_service.stream_chat_response` |
| Sources / multi-file | `thread_sources.py`, `multi_source.py`, `source_policy.py`, `response_evidence.py` |
| Initial Read | `initial_read.py`, `initial_read_pack.py` |
| Vision | `services/vision/current_turn.py` |
| Domain wall | `domain_boundary.py` |
| Frontend | `frontend/src/api/workspaceFiles.js`, `components/FileLibraryOverlay.jsx`, `lib/fileStatus.js` |

---

*Audit of repository state on 2026-09-10. Production flag values and live drain cadence were not observed.*
