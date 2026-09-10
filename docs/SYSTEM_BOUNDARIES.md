# System Boundaries

**Last updated:** 2026-09-10

What each layer **may** and **may not** do. When in doubt, do not cross boundaries without updating this doc.

---

## Provider layer

**Location:** `services/providers/*`

| May | May not |
|-----|---------|
| HTTP to vendor APIs | Tier routing or fallback chains |
| Parse vendor JSON, return content + tokens | Business rules, tenant policy |
| Optional truncation/TTFB diagnostics | Rolling context assembly |
| `ProviderSendResult` transport shape | Persist threads or idempotency |

**Consumers:** `model_gateway` (chat, council, ad-hoc — all paths).

---

## Governance layer (runtime ops)

**Location:** `services/ops/load_governance.py`, idempotency, diagnostics, timeouts

| May | May not |
|-----|---------|
| Concurrency caps, overload 503 | Choose which LLM answers chat |
| In-process idempotency keys | Cross-replica consistency (not yet) |
| JSON logs (English) | User-facing provider labels |
| Request lifecycle events | Modify council routing or prompt assembly |

---

## Hat layer (persistent governance)

**Status:** **Not implemented**

Future: org policies, role hats, approval workflows. **Must not** be implied by current code.

---

## Council layer

**Location:** `services/council_service.py`, `services/copy_paste_service.py`, `services/rolling_context.py`

| May | May not |
|-----|---------|
| Delegate to rolling context + single gateway call | Honor chat `provider_id` toolbar (council uses tier default) |
| Return `mode: "copy_paste"` JSON contract | Run multi-model evaluation or merge steps |
| Stream NDJSON via `chat_service` | Unbounded total time (governed by gateway + load caps) |
| Legacy empty `council[]` / `synthesis: null` fields | Ad-hoc synthesis (route removed) |

---

## Ad-hoc expert layer

**Location:** `services/adhoc_council_service.py`

| May | May not |
|-----|---------|
| Rolling context opinion for a **required** `provider_id` | Synthesis or multi-voice merge |
| Stream or collect full NDJSON response | Call removed `/adhoc/synthesize` |

---

## Persistence layer

**Location:** `database/*`, `services/thread_service.py`, `message_format.py`

| May | May not |
|-----|---------|
| Threads, messages, RLS | Provider HTTP |
| JSON envelopes in `content` | Inject language into stored user text |
| Rehydration decode | Gateway routing |

**Rule:** Store **raw** user chat text; language instruction only in gateway payload.

---

## Runtime layer (API)

**Location:** `main.py`, `services/chat_service.py`, `services/health_service.py`

| May | May not |
|-----|---------|
| Validate bodies, bind tenant | Vendor API calls (delegate to gateway) |
| Compose chat / council / ad-hoc flows | Rolling context prompt logic (delegate to `rolling_context`) |
| `preferred_language` normalize (en/he) | Auto-detect language chain (v1) |

---

## Frontend layer

**Location:** `frontend/src/*`

| May | May not |
|-----|---------|
| Toolbar, threads UI, provider meta | Backend routing rules |
| Send `provider_id` on chat and ad-hoc | Impersonate provider brands |
| Council stream UI (chunk/done) | Expert-phase timers or synthesis buttons |

---

## File Library layer

**Location:** `services/workspace_files/*`, `routers/workspace_files.py`, `routers/document_processing.py`  
**Audit (as-built, 2026-09-10):** `docs/FILE_INFRASTRUCTURE_AUDIT.md`. **Production flags (read-only):** `docs/GATE_A_PRODUCTION_TRUTH.md`.

Canonical user-upload store is `ben.workspace_files` (Workspace == Project). Domain-isolated from News.

| May | May not |
|-----|---------|
| Persist uploads under `projects_root()/_workspace_files/` keyed by org + workspace | Write News tables (`SourceDocumentVersion`, `NewsArticle`, …) |
| Tenant-bind via Gate A (Clerk / beta) — never shared anonymous org | Trust `ENFORCE_AUTH=false` for file CRUD |
| Drain extraction via cron-secret internal routes | Run Initial Read LLM on the extraction drain |
| Inject READY text into **standard streaming chat** only (Gate 3D prefixes or flag-gated Gate 4A FTS) | Inject File Library into council / `expert_opinion` or non-stream `handle_chat` |
| Record used-files / response evidence on the chat envelope | Dump all READY files when source restriction fails (fail closed to empty) |

Do not reuse `services/knowledge_store.py` (SQLite project knowledge uploads) as File Library persistence. That path is adjacent, not canonical.

---

## Cross-layer flows

### Chat

```text
Frontend → POST /chat[/stream] → main → chat_service
  → chat_language (wrap) → model_gateway → provider adapter
  → persist raw message via thread_service
```

### Council (rolling context / copy-paste)

```text
Frontend → POST /council[/stream] → main → council_service
  → copy_paste_service → rolling_context (load history + append question)
  → chat_service stream OR model_gateway route_request
  → persist assistant markdown via thread_service
```

### Ad-hoc expert

```text
Frontend → POST /api/threads/{id}/adhoc/expert[/stream] → main → adhoc_council_service
  → copy_paste_service (provider_id required)
  → same gateway + persist path as council
```
