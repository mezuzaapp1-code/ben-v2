# BEN Engineering Maturity / Execution Foundation Audit

**Mode:** Research + codebase audit only.  
**Date:** 2026-09-13  
**Status:** COMPLETE. DO NOT IMPLEMENT. DO NOT OPEN A GATE.  
**Product P1 (unchanged):** Business Draft + Private Supplier Directory + Procurement Conversation.

This document is **not** the historical `tasks/README.md` P1 (BEN Log event schema — already completed). Those names collide. Here **P1 always means the procurement product gate**.

`decision_003` (no autonomous agents) remains **LOCKED**. FILE OUT remains **DEFERRED**. Auth/RLS, FTS, flags, P2, and connectors are not changed by this audit.

**Verdict:** BEN is a mature **AI chat + file-intelligence** system with a strong **FILE IN job substrate**. It is **not yet** a business work system. The gap is real, but it is **not a P1 blocker**. Do not delay user value to build a generic Task/Connector platform.

---

## 0. Current reality map

### 0.1 What exists today (code, not aspiration)

| Subsystem | Where it lives | What it actually is |
|---|---|---|
| User identity | Clerk JWT → `auth/tenant_binding.py` `TenantContext` | No `users` table. `user_id` is Clerk `sub`. |
| Tenant / org | `TenantContext.tenant_id` used as DB `org_id` | Three types: `organization` (Clerk org UUID), `personal` (UUIDv5 of user), `anonymous` (shared fallback, Gate A isolated from files). |
| Organizations | Clerk, not a BEN table | `org_role` from JWT (`org:admin` / owner). `REQUIRE_ORG_FOR_SIGNED_IN` default **false** (`auth/tenant_policy.py`). |
| Project / Workspace | `ben.projects`; comment on `WorkspaceFile`: “Workspace == Project in BEN V1” | One table. Chat passes `project_id`; files use `workspace_id` FK to `projects.id`. |
| Threads | `ben.threads` (`org_id`, `title`, `source_state` JSONB) | **No `workspace_id` column.** Workspace is request-scoped, not a thread FK. |
| Messages | `ben.messages` | Org + thread. Content may be JSON envelope (`services/message_format.py`). |
| Dual thread store | `database/thread_store.py` SQLite | Legacy per-thread SQLite + `system_main.db`. File source state is **Postgres-owned** (`thread_sources.py` explicitly does not use SQLite). |
| Workspace Files | `ben.workspace_files` + durable `_workspace_files/` | FILE IN only. Authenticated download. Gate A: no anonymous persistence. |
| Document jobs | `ben.document_processing_jobs` | Durable ledger: queued/running/succeeded/failed/cancelled. Leases, attempts, SKIP LOCKED, reaper. Types: `file_extraction`, `structured_extraction`, `file_initial_read`. Bound to `(file_id, org_id, workspace_id)`. |
| Background execution | Cron drain + upload-wake | `services/workspace_files/drain.py`: not a persistent worker loop; bounded batch via secret-gated HTTP. Crash recovery via lease expiry. |
| model_gateway / providers | `services/model_gateway.py`, `services/providers/*` | Single-hop chat; circuit breakers; native **in-process tools**. |
| Add Opinion | `copy_paste_service` / `council_service` (`mode: copy_paste`) | Extra provider pass on rolling thread context. Not a file-retrieval path. |
| Council | Same copy-paste path today | Historical multi-expert panel is not the live path. HTTP/stream bound. |
| Inference metering | `ben.inference_call_records` + `docs/INFERENCE_ACCOUNTING.md` | Append-only per provider **attempt**. `request_id` + `execution_id`. `org_id` is **nullable string**, no RLS in migration 009. |
| Action Cards | Frontend `ActionCard.jsx` + NDJSON `mutated_state` | UI cards from native tool JSON. Not durable actions. WhatsApp is `wa.me` deep link, not a send API. |
| Authorization | Clerk JWT + `set_config('app.current_org_id')` + RLS | Org-scoped. Project create: admin/owner (`auth/project_privileges.py`). Files: org+workspace match. |
| RLS | ENABLE on core tables (001); FORCE on files/jobs/pages/chunks/IR | Table-owner bypass remains on threads/messages/ledger unless FORCE (not applied there). Do not change in this audit. |
| Audit / logging | `services/ops/structured_log.py`, `BenLogEvent`, unused `ledger_*` | Technical logs vs reasoning continuity vs unused governance proof. **No business ActionEvent.** |
| Retries | Provider fallbacks; doc-job exponential backoff; in-process idempotency | Jobs: `compute_retry_delay_seconds`, max_attempts=5. Chat: in-memory registry. |
| Timeouts | `services/ops/timeouts.py` | Chat ~12s (explicit provider 25s). Council stream up to 300s idle. Doc job 120s. |
| Idempotency | `services/ops/idempotency.py` | **In-process dict**, TTL 120s pending / 300s completed. Header `X-BEN-Client-Request-Id`. Not distributed. |
| Scheduled work | News cron secret; doc-processing cron secret | Two secret-gated HTTP drains. No general scheduler. |
| External integrations | Stripe checkout; `wa.me`; Clerk | No OAuth connector store. `global_service_store.py` mentions `oauth_token` as a **memory key name**, not a credential vault. |
| Task/job abstractions | `ProjectTask` (todo list); `DocumentProcessingJob`; `LedgerAction` | Three different meanings of “task/action”. None is a business execution run. |
| ExecutionPlan | `services/execution_plan.py` | Boarding pass. Vision **enforced**. Chat/council **`enforced=False`, `allowed=True`**. `connector_id` is diagnostic (openai_adapter etc.), not a business connector. |
| Native tools | `execute_native_tool` | Quotation/invoice/tender/attendance/upskilling/Basalt. Writes `financial_ledger` / project memory. **Does not send email or WhatsApp.** `export_ledger_to_accountant` returns a text report in JSON. |

### 0.2 Classification (REUSE / HARDEN / EXTEND / NEW / DEFER)

| Primitive | Classification | Why |
|---|---|---|
| Clerk JWT + `TenantContext` | **REUSE** | Canonical identity. |
| `org_id` on product tables + `set_config` | **REUSE** | Tenancy spine. |
| Gate A (no anonymous file persist) | **REUSE** | Keep for any future artifact/action. |
| RLS org isolation | **HARDEN later** (do not touch now) | FORCE missing on older tables; inference ledger has no RLS. Not a P1 blocker. |
| `projects` as Workspace | **REUSE** | Keep Project ≠ future Business. |
| Threads + messages | **EXTEND later** | Optional workspace/business/mode linkage when a conversation must belong to a Business. P1 can use `source_state` without a migration if kept small. |
| `source_state` JSONB | **REUSE** | File pending/active/recent. Can hold a procurement **mode flag** without a new table. Do not dump business objects into it. |
| WorkspaceFile + durable storage + auth download | **REUSE** | FILE IN. Do not overload for FILE OUT or RFQ files. |
| `document_processing_jobs` claim/lease/reaper | **REUSE pattern, do not generalize the table** | File-bound FK. Copy the *mechanics* for a future Action table; do not add `job_type=send_rfq` onto this table. |
| Cron drain + upload-wake | **REUSE** | Fine until wait-for-external work exists. |
| model_gateway + providers | **REUSE** | Chat/Add Opinion. |
| ExecutionPlan | **EXTEND later** | Today diagnostic except vision. Future: enforce **named capabilities** (`email.send`) here, not in the model. |
| Native tools + Action Cards | **DEFER / do not promote** | Construction copilot UX. Not a connector bus. Risk: looks like send/CRM. |
| `ProjectMember` EMPLOYEE/VENDOR | **Do not reuse as Supplier Directory** | Construction roster. P1 suppliers must be a new object. |
| `ProjectTask` | **Do not reuse as execution Run** | Human todo board. |
| `FinancialLedger` | **Do not reuse as RFQ/Quote** | Project cash entries. |
| `LedgerDecision/Approval/Action` | **DEFER** | Governance proof (decision_002). Unused at runtime. Wrong shape for RFQ send (requires a Decision first). |
| BEN Log | **REUSE for reasoning continuity** | Not “what sales did today.” |
| Inference call records | **REUSE / HARDEN later** | AI usage economy. Bind UUID org later; add RLS later. Not P1. |
| In-process idempotency | **HARDEN when first outbound send exists** | Insufficient for “send to five suppliers.” |
| Load governor | **REUSE** | In-process chat/council caps. |
| Stripe | **REUSE for AI packages** | Separate from business-network economy. |
| SQLite thread_store | **HARDEN / shrink later** | Dual store. File path already moved to Postgres. |
| Connectors / credentials / ActionEvent / GeneratedArtifact jobs | **DEFER** | After P1; first send gate. |
| Broker (Kafka/SQS/Redis) | **DEFER** | Not justified now. |
| Generic RBAC | **DEFER** | Clerk org admin is enough for P1 owner-draft. |
| decision_003 lift | **DO NOT** | Locked. |

---

## 1. Identity / tenancy / ownership

### Canonical hierarchy today

```text
Clerk user (sub)
  → TenantContext.tenant_id  ==  DB org_id
       ├─ personal: UUIDv5(user)
       ├─ organization: Clerk org UUID + org_role
       └─ anonymous: shared id (chat only; files forbidden)
            → Project / Workspace (ben.projects)
                 → WorkspaceFile, chunks, jobs
            → Thread (org_id only)
                 → Message, BenLogEvent, source_state files
            → ProjectMember / ProjectTask / FinancialLedger (construction)
```

**There is no Business object.** There is no Connection object. There is no Artifact-out object.

| Object | Canonical owner | Explicit? |
|---|---|---|
| Thread, Message, BenLogEvent | `org_id` | Yes |
| Project | `org_id` | Yes |
| WorkspaceFile, pages, chunks, jobs | `org_id` + `workspace_id` | Yes + composite FK on jobs |
| InferenceCallRecord | `org_id` string nullable | Weak / inferred |
| Chat workspace | `project_id` on HTTP request | Request-scoped, not on Thread |
| SQLite thread metadata | `org_id` in SQLite | Parallel store; WorkspaceResolver ignores thread project fields on org mismatch |
| Ledger_* | `org_id` | Yes, unused |

### Business vs Project

**Keep them separate.** `projects` is the **workspace / file library / chat project**. A future **Business** is a legal/commercial identity (draft in P1). Binding a procurement conversation to a Business must not collapse into `projects`.

Current auth/RLS can support P1 **if** new tables carry `org_id` and use the same `set_config` pattern. It cannot express “salesperson vs accounting vs warehouse” on the same org. Clerk `org_role` is admin-or-not for **project create**, not data-domain RBAC.

### Concrete risks (do not fix now)

1. Thread not bound to workspace → same org can attach the wrong project’s files if the client sends a mismatched `project_id` (server still checks project∈org, not thread∈workspace).
2. Personal tenant UUID ≠ Clerk org UUID → “Business” on a personal tenant is a product choice, not a schema accident.
3. Table-owner RLS bypass on ENABLE-only tables.
4. Overloading `ProjectMember.member_type=VENDOR` as the supplier directory.

**P1 implication:** New `Business` (draft) + private suppliers under `org_id` (+ optional `workspace_id` or `business_id`). Do not alter RLS policies in P1 beyond the same ENABLE/FORCE pattern already used for new tables. Do not change existing policies.

---

## 2. Conversation ≠ Task

**Today, model execution belongs to the HTTP request / NDJSON stream.**

Evidence:

- `stream_chat_response` / `run_council` run inside the request.
- Timeouts are **user-facing route budgets** (`timeouts.py`), not job leases.
- Load governor tracks **in-flight HTTP** (`chat_active`, `council_active`).
- Idempotency registry is request-scoped memory.
- Native tools execute **inside** that request (`execute_native_tool`).

The **exception** is document processing: work **outlives** upload HTTP via `document_processing_jobs` + drain. That is FILE IN, not “send RFQ / wait for reply.”

### Does BEN need Task / Run / Action?

**Yes, eventually** — at the first **outbound or long-wait** product (P2-class RFQ send, email, CRM write). **Not for P1.**

Suggested later object (hypothesis, not a schema):

| Field | Role |
|---|---|
| Action / Run id | Durable identity |
| org, business, thread, actor | Ownership |
| capability | `procurement.rfq.send` not free-form |
| state | CREATED / QUEUED / RUNNING / WAITING_FOR_HUMAN / WAITING_FOR_EXTERNAL / COMPLETED / FAILED / CANCELLED |
| attempt | Separate from action |
| idempotency key | Client + action |

The listed state machine is **reasonable** for send-and-wait. Do not copy it onto `document_processing_jobs` (no WAITING_* states; file FK).

### Generalize `document_processing_jobs`?

**No.** That would be the wrong abstraction:

- Composite FK to `workspace_files`
- Partial unique index on file+job_type+versions
- Drain executes extraction pipelines only
- `WAITING_FOR_EXTERNAL` does not belong on an extraction lease

**Do copy:** SKIP LOCKED claim, lease, reaper, attempts, bounded exponential backoff, `runner_eligible`, secret-gated drain, tenant composite ownership.

---

## 3. Action execution contract

**Today’s boundary**

```text
User message
  → chat_service (HTTP)
  → ExecutionPlan (mostly diagnostic)
  → model_gateway (provider call)
  → optional native tool (in-process, org+project scoped)
  → Action Card JSON in stream
  → Message persist
```

Deterministic enforcement today:

- Auth/tenant from JWT, never client JSON (`tenant_binding.py`).
- File download: JWT + org + workspace match.
- Vision capability: ExecutionPlan `enforced=True`.
- Native tools: allowlisted names; `_require_project(org, project)`.
- `decision_003`: no agent loop.

Missing for business execution:

- Named **capabilities** as the permission object (`email.send`).
- Policy check **before** side effects, independent of model text.
- Proof of external success (provider receipt), not model “I sent it.”
- Scope freeze (five named supplier ids), not “suppliers like these.”
- Payment destination / bank details never model-writable.

**Minimum future contract (when first write happens):**

1. Structured intent (ids, capability, payload schema).
2. Authorization against org/role/capability grant.
3. Human approval for writes (Observe → Recommend → Approve → Execute).
4. Durable Action + attempt.
5. Connector returns Result; only then mark COMPLETED.
6. ActionEvent append-only.
7. Model never grants capabilities or invents success.

P1 (directory + conversation) needs **none** of this beyond ordinary JWT/RLS.

---

## 4. Idempotency / retry / failure

| Concern | Current | Gap for “RFQ to five suppliers” |
|---|---|---|
| Duplicate HTTP | In-process idempotency + 409 if pending | Lost on process restart; not shared across workers |
| Browser retry | Client request id on chat/council | Same |
| Provider retry | Gateway attempts / fallbacks; each attempt metered | OK for AI; must not retry **sends** the same way |
| Background retry | Doc jobs: attempts, backoff, max 5, deterministic no-retry codes | Pattern OK; table wrong |
| Partial execution | Extraction is per-file; tools are all-or-nothing in one request | **No per-recipient send record** |
| Timeouts | HTTP budgets | Would abort mid-send with no durable partial state |
| Process crash | Doc jobs recover via lease; chat does not | In-flight send would vanish or duplicate |
| Duplicate sends | **No action identity** | **This is the #1 future send failure** |
| Stale jobs | Doc reaper | N/A for chat |

“Send 1–3, crash, retry, duplicate 1–3” is **unguarded** today. That is expected: BEN does not send.

**When the first send exists, minimum:** idempotency key + per-target send row (PENDING/SENT/FAILED) + attempt_id + no retry of SENT. Do not build this in P1.

---

## 5. Connector foundation

**There is no Connector / Connection / Credential abstraction.**

Closest things:

- Provider **adapters** (OpenAI/Anthropic/Google/xAI) — AI, not business systems.
- ExecutionPlan `connector_id` — diagnostic label for those adapters.
- `wa.me` client navigation (`frontend/src/mobile/whatsapp.js`).
- Stripe Checkout (`billing/stripe_service.py`) — AI Pro subscription, localhost success URLs.
- Native tools pretending operational outcomes (ledger row, Action Card).

Future contract (defer): org-owned Connection, secret reference (not in chat), typed capabilities (`email.send`, `crm.read_customer`), health, revocation, rate limits, structured errors. **Typed capabilities, not arbitrary tools.** Matches ExecutionPlan’s unused enforcement slot.

Do not implement. Do not lift decision_003 to get “tools.”

---

## 6. People / roles / permissions

| Layer | Exists? | Fit for Owner/Manager/Sales/Accounting/Warehouse/Supplier |
|---|---|---|
| Clerk org_role | Yes (admin/owner vs member) | Coarse. Enough for P1 **owner-draft**. |
| `can_create_project` | Yes | Not data-domain. |
| `ProjectMember` role string | Yes | Construction crew, not org IAM. |
| Hats | Queued metadata-only (`tasks/queued/hats_v1.md`) | Explicitly **not** authority. |
| External supplier login | No | P1 suppliers are **private directory**, not portal users. |

**Gap:** no Person→Org→Role→Permission→Action matrix. **Do not build RBAC for P1.** First need is owner-only draft Business + suppliers in the caller’s org.

---

## 7. Action event / audit trail

Three existing trails, all the **wrong** answer to “what did sales do today?”

| Primitive | Audience | Content |
|---|---|---|
| Structured logs | Engineers | request_id, subsystems, no business target |
| BEN Log | Continuity | prompt/response/decision/next_step on a **thread** |
| Ledger L1 | Governance proof | Unused; decision→approval→action |
| Inference ledger | Cost | tokens, not RFQs |

**Need later:** append-only **ActionEvent** (who, org/business, conversation, requested vs actual, capability, target ids, result, approval, timestamp, run/attempt). Facts, not model summary of chat.

Reuse: append-only style of inference records + BEN Log’s org/thread indexes. Do not overload BEN Log event types (locked in `decision_005`). Do not activate Ledger L2 as the sales audit (`decision_002`).

P1 does not need ActionEvent. Creating suppliers is ordinary CRUD; optional `created_by` on those rows is enough.

---

## 8. Human-in-the-loop

`decision_003` locked. Architecture principle 6 already says destructive actions need explicit operator decision.

LedgerApproval is a **governance** verdict on a Decision, not “approve this RFQ send.” Action Cards are **display + client deep link**, not an approval record.

**Generic approval object:** **later**, at first write/send. **Not now.** P1 is draft data + conversation, no execute.

Autonomy must be earned per capability; default remains Observe → Recommend → Human Approves → Execute.

---

## 9. Context / business data

FILE retrieval/evidence is **in progress and must not change** in this audit.

Pattern already correct for files: **source of truth in Postgres/durable bytes → structured query/FTS → bounded context assembly → model**. Chunk retriever + evidence, not dump-the-library.

Future CRM/suppliers/orders/ActionEvents must follow the same split:

- SoT: tables with org/business FKs
- Query: filtered lists (e.g. “my private suppliers”), never 100k rows in the prompt
- Retrieval: only if unstructured docs
- Context: small relevant slice + ids
- Model: reason over that slice

P1 private supplier directory: **query by org (+ business)**, inject a **short list or selected ids** into a procurement thread. Do not FTS-index supplier rows as WorkspaceFiles.

---

## 10. Observability

| Layer | Trace ids today |
|---|---|
| HTTP | `request_id` contextvar |
| Inference | `execution_id` + per-attempt row |
| Files/jobs | `job_id`, org, workspace, file |
| Chat | thread_id on persist |
| Action / connector | **Absent** |

Can trace User → Conversation → Model → Retrieval **within one request**. Cannot trace Action → Connector → Result because those objects do not exist.

**Do not add a new observability product.** Extend structured logs + inference-style append-only rows when actions exist (`action_id`, `attempt_id`). Metrics already: latency, tokens, cost, job attempts, load-governor rejects.

---

## 11. Cost / resource control

**AI usage economy (exists):** inference_call_records: who (`user_id` nullable), org (string), workspace (string), provider/model, tokens, estimated USD, retries as **separate rows**, outcome classes.

Gaps: no thread_id on the ledger row; org not UUID/RLS; persist failure is **soft** (user continues). Stripe is a coarse Pro gate, not per-token billing.

**Business network economy (does not exist):** RFQ sends, WhatsApp, CRM API, artifacts. Keep **separate** from token packages.

Future non-token costs (defer accounting): storage, doc processing minutes, connector calls, workers, GeneratedArtifact bytes. Do not redesign pricing.

---

## 12. Background execution / scale

**KEEP NOW**

- One web process + secret-gated **bounded drain**
- Postgres `document_processing_jobs` + SKIP LOCKED + leases
- Upload-wake for the just-uploaded file
- In-process load governor for chat/council
- No Kafka/SQS/Redis

**TRIGGER TO MIGRATE** (measured, not fashionable)

- Drain cron cannot meet FILE IN SLO (queue depth / `available_at` lag) at current `BEN_DOC_DRAIN_LIMIT`
- Lease contention / reaper storms across many orgs
- Need **WAITING_FOR_EXTERNAL** hours-long work with per-org fairness
- Multiple drain replicas fighting without SKIP LOCKED remaining sufficient (it usually remains sufficient on Postgres)

**CANDIDATE FUTURE**

1. Same Postgres claim pattern on a **new** `business_actions` (or similar) table.
2. Only then a broker, if multi-region workers or extremely bursty connector traffic is measured.

Per-org fairness: **not implemented** on doc jobs (global claim by `available_at`). Acceptable for FILE IN. Required before multi-tenant mass send.

---

## 13. Generated business artifacts

FILE OUT research is complete and **DEFERRED**. Do not reopen.

This audit’s only interaction: **do not hang GeneratedArtifact generation on `document_processing_jobs`** (file FK). A future renderer job should be a sibling job family or inline write_bytes for V1 FILE-OUT 1. Execution-foundation (Action vs HTTP) does not block FILE-OUT 1’s proposed snapshot path. No implementation.

---

## 14. P1 compatibility

P1 = Business Draft + Private Supplier Directory + Procurement Conversation.

| Temptation | Verdict |
|---|---|
| Wait for Task/Run platform | **No.** P1 has no send, no wait, no connector. |
| Reuse ProjectMember as suppliers | **No.** New directory. |
| Reuse FinancialLedger as quotes | **No.** |
| Wire native tools / wa.me as “network” | **No.** Client deep links are not P1. |
| Add workspace_id FK on threads | **Not required** if procurement thread stores `mode` in `source_state` and Business id as an explicit column **only if** you already migrate; prefer smallest: thread title + source_state.mode + business_id nullable **if** a Business table exists. |
| Change RLS/auth/FTS | **No** unless a new table’s own ENABLE/FORCE copy. |
| Expand P1 to send RFQ | **No** — that is later (P2-class). |

**Concrete blocker to P1?** **None.** Auth can own new org-scoped rows. Conversation already exists. Files stay unused or optional uploads without becoming the supplier SoT.

Technical debt to **avoid during P1** (not reasons to delay P1): do not encode suppliers in chat JSON as SoT; do not use `projects` as Business; do not call the conversation an “agent.”

---

## 15. Failure frontier (ranked)

Scores are for the **chat → business execution** transition, not for P1 ship.

### F1. HTTP request = unit of work

- **Evidence:** chat/council/native tools in-request; timeouts.py; load governor.
- **Failure:** RFQ/email/CRM work dies or double-starts on retry.
- **User impact:** Silent non-send or duplicate supplier spam.
- **Likelihood:** Certain if send is built on chat HTTP. **Severity:** High.
- **Mitigation:** None for sends. Doc jobs prove the alternative pattern.
- **Minimum fix:** Durable Action + per-target state **at first send**.
- **When:** First outbound gate — **not P1**.

### F2. No idempotent send identity

- **Evidence:** in-process `IdempotencyRegistry`; comment “not distributed.”
- **Failure:** Worker retry resends to 1–3.
- **User impact:** Duplicate RFQs. Trust loss.
- **Likelihood:** High on any retried write. **Severity:** High.
- **Mitigation:** Chat 409-in-flight only.
- **Minimum fix:** DB unique (org, idempotency_key) + SENT rows.
- **When:** First send.

### F3. Tools that look like they acted

- **Evidence:** `issue_customer_invoice` writes `financial_ledger` pending; Action Card; `wa.me` is a link; `export_ledger_to_accountant` is a string report.
- **Failure:** Model/UI claims “sent / invoiced / exported.”
- **User impact:** False operational confidence.
- **Likelihood:** Medium if P1 reuses copilot tools. **Severity:** High if believed.
- **Mitigation:** Architecture principles 2 and 4; copy-paste council honesty.
- **Minimum fix:** Do not reuse native tools for P1. Honest copy: “draft only.”
- **When:** Now as a **P1 design constraint**, not a code rewrite.

### F4. Wrong objects reused as business SoT

- **Evidence:** ProjectMember VENDOR, ProjectTask, FinancialLedger, LedgerAction.
- **Failure:** Procurement data trapped in construction schemas; RLS/product confusion.
- **User impact:** Unqueryable “suppliers”; broken reports.
- **Likelihood:** Medium under schedule pressure. **Severity:** High.
- **Mitigation:** This audit.
- **Minimum fix:** New Business + Supplier tables in P1.
- **When:** P1 (schema choice), without extra platforms.

### F5. Thread not owned by workspace/business

- **Evidence:** `Thread` has `org_id` only; workspace via request `project_id`.
- **Failure:** Cross-project context bleed inside one org.
- **User impact:** Wrong files/suppliers in a procurement chat.
- **Likelihood:** Medium. **Severity:** Medium.
- **Mitigation:** File APIs require workspace match; retrieval is workspace-scoped.
- **Minimum fix:** P1 bind conversation → Business (and workspace if files). Small column or source_state + server checks.
- **When:** P1 if a procurement thread is created; still not a platform rewrite.

### F6. ExecutionPlan does not enforce business capabilities

- **Evidence:** `enforced=False` for non-vision; `allowed=True`.
- **Failure:** Model-selected tool/connector later bypasses policy.
- **User impact:** Unauthorized send/CRM write.
- **Likelihood:** High if ACE tools are bolted on. **Severity:** High.
- **Mitigation:** Native tool allowlist + project require; decision_003.
- **Minimum fix:** Capability allowlist server-side at first write.
- **When:** First write. Keep decision_003.

### F7. Dual conversation stores + in-process limits

- **Evidence:** SQLite thread_store vs Postgres messages; in-process idempotency and load governor.
- **Failure:** Split-brain metadata; multi-instance duplicate councils.
- **User impact:** Lost continuity; overload not global.
- **Likelihood:** Medium at >1 app replica. **Severity:** Medium.
- **Mitigation:** File source_state already Postgres-only.
- **Minimum fix:** Don’t add more SQLite SoT. Postgres idempotency when multi-replica **and** send/council dedup matters.
- **When:** Multi-replica production pain, or first send — not P1.

### F8. “What did sales do today?” answered from chat

- **Evidence:** No ActionEvent; BEN Log is prompts.
- **Failure:** Hallucinated activity reports.
- **User impact:** Bad management decisions.
- **Likelihood:** Certain if asked today. **Severity:** Medium (feature absent).
- **Mitigation:** Don’t ship that question.
- **Minimum fix:** ActionEvent when actions exist.
- **When:** After first real actions.

---

## 16. Maturity scorecard (today)

| Area | Score | Evidence |
|---|---|---|
| Identity/Tenancy | **7** | Clerk JWT, personal/org/anonymous, org_id spine, Gate A. No User/Business table. |
| Authorization | **6** | RLS + JWT + file org/workspace checks. Admin-only project create. No domain RBAC. ENABLE vs FORCE inconsistency. |
| Conversation Core | **8** | Streaming chat, copy-paste Add Opinion, envelopes, rolling context, persist. Dual SQLite is a scar. |
| File Infrastructure | **8** | Durable org-scoped storage, statuses, checksums, auth download. |
| Retrieval/Evidence | **8** | Chunks, FTS, evidence assembly, workspace scoping. (Do not change.) |
| Background Jobs | **7** | Excellent **file** job ledger (claim/lease/reaper/backoff). Cron drain, not a general executor. |
| Execution Model | **3** | HTTP = work. ExecutionPlan mostly diagnostic. Native tools in-request. |
| Idempotency | **4** | Chat/council in-memory; file enqueue ON CONFLICT. No action/attempt identity. |
| Connectors | **1** | Deep links + Stripe + AI providers. No Connection/capability/secret lifecycle. |
| Audit Trail | **4** | Strong technical logs + BEN Log; unused Ledger; no operational ActionEvent. |
| Observability | **5** | request_id, execution_id, inference rows, job ids. No action/connector trace. |
| Cost Control | **6** | Attempt-level token/cost ledger. Soft persist. Not tied to thread/action. Stripe ≠ usage. |
| Human Approval | **3** | Principle + unused LedgerApproval + UI cards. No execute-approval object. |
| Business Data Model | **2** | Construction project ops + news. No Business/Supplier/RFQ/Quote/Customer. |
| Scale Readiness | **5** | Fine for chat+FILE IN on Postgres jobs. In-process caps. No per-org fair send queue. |

Do not average these into a vanity “BEN is 7/10.” Chat/files are strong; **execution/connectors/business data are not.**

---

## 17. Minimal target architecture

Hypothesis vs codebase:

```text
User (Clerk)
  → Conversation (threads/messages)     [EXISTS]
  → Intent / Reasoning (model_gateway)  [EXISTS, single-hop]
  → Execution Boundary                  [MISSING except vision + tool allowlist]
  → Permission / Policy                 [ORG RLS only; ExecutionPlan weak]
  → Task / Action                       [MISSING; do not use ProjectTask or doc jobs]
  → Connector / Internal capability     [MISSING; do not use native tools as connectors]
  → External System                     [wa.me / Stripe / providers only]
  → Result / ActionEvent                [MISSING]
  → Conversation                        [EXISTS]
```

**Next-stage (post-P1, still small):** keep User→Conversation→Reasoning as now. Add **Business + Suppliers** as SoT queried into procurement chat. When sending exists, insert Execution Boundary + Action + Connector **in front of side effects**, then ActionEvent, then a chat ref (like `generated_artifact_ref` later).

P1 implements only the **data + conversation** slice of that diagram.

---

## 18. Staged engineering maturity roadmap

Do **not** turn this into active tasks automatically. Research ≠ queued.

### Stage A — P1 product (NOW)

- **Problem:** No business identity or private suppliers; chat is generic.
- **Why now:** User value; no execution platform required.
- **Reuse:** TenantContext, org RLS pattern, threads/messages, source_state, chat stream.
- **Minimum change:** `Business` draft + private suppliers + procurement thread mode. Owner-only. No send.
- **Tests:** org isolation, no cross-org suppliers, conversation stays in org, no FTS/auth redesign.
- **PASS:** Owner can draft a Business, add suppliers, talk in a procurement-mode thread.
- **Rollback:** Drop/disable new tables/UI; chat unchanged.
- **Deps:** None of F1–F2 platform.
- **Deferred:** Connectors, Task/Run, ActionEvent, FILE OUT, RBAC, decision_003 lift.

### Stage B — Honest side-effect boundary (when P2-class send is scheduled)

- **Problem:** F1–F3.
- **Why now:** First `mailto`/`wa.me` **assisted** send still must not claim success; first **automated** send needs Action rows.
- **Reuse:** Doc-job claim/lease **pattern**; ExecutionPlan slot; JWT.
- **Minimum:** Capability name + per-target state + “draft vs sent” honesty. Human approve before send.
- **PASS:** Retry cannot duplicate a SENT target. UI cannot say sent without receipt.
- **Deferred:** CRM/ERP, brokers, generic workflow engine (`tasks/queued/workflow_engine.md` stays inactive).

### Stage C — Operational facts

- **Problem:** F8.
- **Reuse:** Append-only inference style.
- **Minimum:** ActionEvent for executed capabilities only.
- **PASS:** “What did we send today?” answered from events, not LLM over chat.

### Stage D — Scale the job table you already have

- **Problem:** FILE IN / future action queue depth.
- **Keep Postgres SKIP LOCKED** until measured trigger in §12.
- **Do not** introduce Kafka because it is popular.

---

## 19. Final decision

### 1. Current BEN engineering map

AI chat system: Clerk tenancy, org-scoped Postgres, streaming chat, Add Opinion, FILE IN (durable files + FTS + evidence), durable **file** jobs, inference accounting, construction copilot tools/Action Cards. Not a business execution runtime.

### 2. REUSE / HARDEN / EXTEND / NEW / DEFER

See §0.2. Headline: **reuse tenancy + chat + FILE IN jobs pattern; new Business/Supplier in P1; defer Task/Connector/ActionEvent/broker.**

### 3. Top engineering risks

F1 HTTP-as-work, F2 no send idempotency, F3 fake-success tools, F4 schema reuse, F5 thread/workspace bind, F6 unenforced ExecutionPlan, F7 in-process/multi-store, F8 no operational events.

### 4. Failure frontier

The frontier is **side-effectful, retried, multi-target work**. Chat+files are on the safe side of that frontier. P1 stays on the safe side if it does not send.

### 5. Maturity scorecard

§16. Strong: conversation, files, retrieval, tenancy. Weak: execution, connectors, business model, approval, action audit.

### 6. Minimal target architecture

§17. Do not implement the missing middle until a send/write gate.

### 7. P1 compatibility

**Compatible. No concrete architectural blocker.** Do not expand P1. Do not reuse ProjectMember/FinancialLedger/doc jobs/native tools as the network.

### 8. Staged roadmap

P1 now → send/idempotency when outbound exists → ActionEvent → measure before brokers.

### 9. What not to build

- Workflow engine, agent runtime, decision_003 lift
- Generic Task table “just in case”
- Kafka/SQS/Redis
- Connector OAuth mesh
- Org-wide RBAC
- ActionEvent platform
- FILE OUT
- Ledger L2 as sales audit
- Generalizing `document_processing_jobs` to RFQs
- Auth/RLS/FTS rewrites

### 10. Exact recommended next gate

**P1 — Business Draft + Private Supplier Directory + Procurement Conversation.**

No automatic infrastructure gate. No P2. No FILE OUT. No connectors.

**STOP.**
