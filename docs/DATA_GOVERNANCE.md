# BEN Data Governance (v1)

Normative policy for what cognitive data is stored, how it is scoped, and what is explicitly out of scope.

This document complements `docs/BEN_RUNTIME_CONTRACTS.md` and `docs/BEN_SYSTEM_MAP.md`.

---

## 1. Persistence ownership map

| Data | Store | Tenant key | Thread scope | Notes |
|------|-------|------------|--------------|-------|
| **Threads** | `ben.threads` | `org_id` | N/A | Title + timestamps; created via `resolve_thread_id()` |
| **Messages (chat)** | `ben.messages` | `org_id` | `thread_id` FK | User plain text; assistant JSON envelope (`kind=chat`) |
| **Council transcript** | `ben.messages` | `org_id` | `thread_id` FK | User question + `council_expert` + optional `council_synthesis` envelopes |
| **Synthesis object** | `ben.knowledge_objects` | `org_id` | **Not thread-linked in v1** | `type=synthesis`, `content` = synthesis JSON; optional parallel to thread |
| **Cognitive events** | `ben.cognitive_events` | `org_id` | `thread_id` FK | Schema exists; **runtime does not write in v1** |
| **Relationships** | `ben.relationships` | `org_id` | via KO ids | Schema exists; **runtime unused in v1** |
| **Runtime / idempotency** | In-process only | `tenant_hash` + route | N/A | Not durable; lost on restart / per replica |
| **Diagnostics** | Logs + `/runtime/snapshot` | `tenant_hash` only | N/A | **No prompts, no message bodies** |

### Dual-store council note (R-027)

Council HTTP may produce:

1. **Thread transcript** — durable conversation history for rehydration.
2. **Knowledge object** — org-scoped synthesis artifact (background, best-effort).

These are **not transactionally linked**. Idempotency persist markers prevent duplicate writes on retry within one process.

---

## 2. What is stored

- Thread metadata (title, timestamps).
- User message text for chat and council questions.
- Assistant content as JSON envelopes with provider, model, outcome, cost metadata for council.
- Synthesis JSON in KO `content` when background persist succeeds.
- Operational aggregates in runtime metrics (counts, durations — no content).

---

## 3. What is not stored

- Full prompt replay cache for idempotency (response envelope only, prompts stripped on replay).
- JWTs, API keys, emails in diagnostics.
- Client `client_request_id` in database (in-process idempotency only).
- Vector embeddings / memory graph (not implemented).
- Distributed dedupe or cross-replica idempotency state.

---

## 4. Tenant ownership

- All durable rows are keyed by `org_id` (tenant UUID).
- **Anonymous:** shared `BEN_ANONYMOUS_ORG_ID`.
- **Personal:** deterministic UUID v5 from Clerk `user_id` (`auth/tenant_ids.py`).
- **Organization:** Clerk JWT `org_id`.
- Body `tenant_id` is validate-only when signed; unsigned mode ignores forged body tenant.
- Reads/writes use PostgreSQL session `app.current_org_id` + application checks (`thread.org_id` match).

---

## 5. Retention assumptions (v1)

- **No automated retention or purge** is implemented.
- Data persists until manual operator action or future retention job.
- Idempotency registry TTL: completed ~300s, pending ~120s (in-process only).

---

## 6. Deletion policy (future)

| Target | Planned approach |
|--------|------------------|
| Thread + messages | CASCADE on thread delete (FK exists) |
| Knowledge objects | Explicit delete API / admin tool (not v1) |
| Tenant offboarding | Org-scoped purge job (not v1) |

---

## 7. Audit policy (future)

- Structured integrity codes on rehydrate (`integrity_warnings` on thread detail when findings exist).
- No content-bearing audit log export in v1.
- Future: append-only audit table for admin actions (export, delete, impersonation).

---

## 8. Privacy constraints

- Integrity helpers and diagnostics use **codes and counts only** — never log or return raw user questions in integrity reports.
- `GET /api/threads/{id}` returns message content to **authenticated tenant scope only** (required for UI rehydration).
- Replay responses omit stored `question` / `message` fields.

---

## 9. No prompt / content logging policy

Forbidden in `subsystem=persistence_integrity`, runtime diagnostics, and overload paths:

- Prompts, questions, council responses, synthesis text in logs
- JWT, API keys, raw tenant UUID in public diagnostics

Allowed: `integrity_codes[]`, `tenant_hash`, `request_id`, outcome enums.

---

## 10. Verification gates

| Gate | Automated | Browser |
|------|-----------|---------|
| Message tenant/thread invariants | `pytest tests/test_persistence_integrity.py` | NOT VERIFIED |
| Council persist dedupe on retry | pytest + idempotency tests | NOT VERIFIED |
| Background persist non-blocking | pytest | NOT VERIFIED |
| Legacy message rehydrate | pytest | NOT VERIFIED |
| Cross-tenant read blocked | pytest (404 contract) | NOT VERIFIED |

Do not mark persistence integrity risks **FIXED** until browser refresh + retry matrix is executed.
