# BEN Runtime Contracts

Normative operational guarantees for the BEN cognitive runtime.

---

## 7. Load Governance (v1)

### 7.1 Bounded concurrency

| Route | Default limit | Env override | Behavior |
|-------|---------------|--------------|----------|
| `POST /chat` | 8 concurrent | `BEN_MAX_CONCURRENT_CHAT` | Immediate reject when saturated (no queue growth) |
| `POST /council` | 2 concurrent | `BEN_MAX_CONCURRENT_COUNCIL` | Immediate reject when saturated |
| Combined inflight | 12 total | `BEN_MAX_TOTAL_INFLIGHT` | `retry_later` when chat+council active ≥ cap |

Implementation: in-process `LoadGovernor` (`services/ops/load_governance.py`). No background worker queue.

### 7.2 Overload semantics

Structured `detail` object (never raw stack traces to clients):

| Code | HTTP | When |
|------|------|------|
| `runtime_saturated` | 503 | Chat concurrency cap |
| `council_busy` | 503 | Council concurrency cap |
| `duplicate_request` | 429 | Same tenant + normalized question already in-flight |
| `retry_later` | 503 | Total inflight cap |

Fields: `code`, `message`, `hint`, `recoverable: true`, `retry_after_s` (default 5).

Messages localized via `Accept-Language` or prompt script detection (`en` / `he` / `ar`).

### 7.3 Load shedding principles

- **No unbounded waits** for capacity; reject fast.
- **No permanent queue growth** in v1.
- **Duplicate guard** window: `BEN_COUNCIL_DEDUP_WINDOW_S` (default 45s) for in-flight keys only.
- Client mirrors: `loadGovernance.js` blocks double-submit and rapid duplicate clicks.

### 7.4 Runtime metrics hooks

Structured logs (`subsystem=load_governance`):

- `active_chat_requests`
- `active_council_requests`
- `rejected_overload_requests`
- `council_duration_ms` (on council completion)

### 7.5 Verification gates

| Gate | Automated | Browser |
|------|-----------|---------|
| Council concurrency reject | `pytest tests/test_load_governance.py` | NOT VERIFIED |
| Duplicate council guard | pytest + `test-load-governance.mjs` | NOT VERIFIED |
| UI button recovery | — | NOT VERIFIED |
| Localized overload copy | pytest `test_overload_detail_hebrew` | NOT VERIFIED |

Do not mark **R-015** / overload risks **FIXED** until browser verification (spam click, parallel council, refresh during council, timeout recovery).

---

## 8. Observability & Runtime Diagnostics (v1)

### 8.1 Observability guarantees

- Every traced `POST /chat` and `POST /council` emits `request_started` and `request_completed` or `request_failed`.
- Council emits `council_started` and `council_completed` with expert outcome counts and `synthesis_outcome` (no message bodies).
- Provider calls record `duration_ms` and normalized `outcome` ∈ {`ok`, `timeout`, `degraded`, `error`} for OpenAI, Anthropic, Google/Gemini, and synthesis.
- Load rejections emit `overload_rejected` with `overload_code` and route.
- Background persist failures emit `persistence_failed` with operation name only.

### 8.2 Forbidden log payload

Must **never** appear in diagnostics logs or `/runtime/snapshot`:

- Prompts, questions, message content, responses
- JWTs, API keys, emails, raw `tenant_id` / `user_id`
- Full Authorization headers

Allowed: `tenant_hash` (SHA-256 prefix), `tenant_type`, `dominant_language`, `request_id`, aggregates.

### 8.3 Runtime snapshot (`GET /runtime/snapshot`)

Safe operational fields:

- `active_chat_requests`, `active_council_requests`, `inflight_total`
- `rejected_overload_requests`, `overload_rejected_counts`
- `provider_timeout_counts`, `provider_*_counts`, `provider_duration_ms_total`
- `degraded_council_count`, `council_completed_count`, `council_duration_ms_total`
- `persistence_failed_count`, synthesis outcome counters

Emits `runtime_snapshot` diagnostic event when queried. No secrets.

### 8.4 Saturation diagnostics

Under overload, expect `overload_rejected` events and monotonic `rejected_overload_requests`. Snapshot must reflect inflight and rejection counters coherently with load governor state.

### 8.5 Verification gates

| Gate | Automated | Browser |
|------|-----------|---------|
| Chat lifecycle events | `pytest tests/test_runtime_diagnostics.py` | NOT VERIFIED |
| Council provider timing | council integration + metrics store | NOT VERIFIED |
| Snapshot accuracy | pytest | NOT VERIFIED |
| No PII/prompt leakage | pytest caplog | NOT VERIFIED |
| Stress / refresh matrix | — | NOT VERIFIED |

Do not mark **R-019** / observability risks **FIXED** until browser verification under council load.

---

## 9. Runtime Recovery & Idempotency (v1)

### 9.1 Idempotency guarantees

- Clients may send `client_request_id` (body) or `X-BEN-Client-Request-Id` (header), max 128 chars.
- Key: `{route}:{tenant_hash}:{client_request_id}` — in-process registry only (not distributed).
- **Pending:** duplicate submit with same id → **409** `idempotency_rejected` (no second council execution).
- **Completed (TTL default 300s):** same id returns cached JSON response with `idempotent_replay: true` (`replay_detected` event).
- **Failed / released:** pending slot cleared on HTTP error so deterministic retry with same id is allowed.
- No full prompt storage for replay — only last response envelope.

### 9.2 Retry semantics

| Situation | Behavior |
|-----------|----------|
| Retry after success (same `client_request_id`) | Deterministic replay of response |
| Retry while pending | 409 rejected |
| Retry after server error | Allowed (pending released) |
| No `client_request_id` | Idempotency bypassed (load governance dedup may still apply) |

### 9.3 Normalized runtime states

| State | Meaning |
|-------|---------|
| `council_pending` | Idempotency slot acquired |
| `council_running` | Council execution in progress |
| `council_completed` | All experts ok + synthesis |
| `council_degraded` | Partial expert/synthesis degradation |
| `council_failed` | No usable expert/synthesis outcome |
| `persistence_pending` | Background transcript/KO persist scheduled |
| `persistence_completed` | Transcript persist marker recorded |
| `persistence_failed` | Persist logged; may retry on new council |

### 9.4 Persistence recovery

- `council_transcript` and `synthesis_ko` persist markers per idempotency key prevent duplicate rows on retry/replay.
- Background persist failures emit `persistence_failed`; successful deduped persist emits `persistence_recovery`.

### 9.5 Refresh / stale client recovery

- Frontend stores pending council submit in `sessionStorage`; after **40s** refresh clears stale loading (`stale_runtime_state_recovered` on server when pending TTL expires).
- `finally` always clears loading, council status, and pending marker.

### 9.6 Diagnostics events

`idempotency_rejected`, `replay_detected`, `stale_runtime_state_recovered`, `persistence_recovery`.

### 9.7 Verification gates

| Gate | Automated | Browser |
|------|-----------|---------|
| Idempotency replay | pytest | NOT VERIFIED |
| Duplicate pending reject | pytest | NOT VERIFIED |
| Persist dedupe | pytest | NOT VERIFIED |
| Refresh stale UI | `test-runtime-recovery.mjs` | NOT VERIFIED |

---

## 10. Persistence Integrity & Data Governance (v1)

### 10.0 Council Durability Contract (normative)

**Council Durability Contract:**

- Tier-1 store: `ben.messages` / thread transcript.
- Tier-1 transcript persistence must complete before `/council` returns HTTP 200.
- HTTP 200 from `/council` means thread reload via `GET /api/threads/{thread_id}` is valid.
- Transcript persistence failure must return **503** `council_persistence_failed` and release idempotency for retry.
- Tier-2 store: `ben.knowledge_objects` / synthesis KO.
- KO persistence is best-effort background work.
- KO failure must not block `/council` HTTP 200 if Tier-1 transcript persistence succeeded.

### 10.1 Ownership boundaries

See `docs/DATA_GOVERNANCE.md` for the full ownership map. Summary:

- **Durable conversation state:** `threads` + `messages` only.
- **Council synthesis artifact:** optional `knowledge_objects` (parallel, not FK-linked to thread).
- **Runtime/idempotency/diagnostics:** non-durable, per-process.

### 10.2 Persistence integrity guarantees

| Invariant | Guarantee |
|-----------|-----------|
| Message tenant scope | Every message `org_id` matches bound tenant; cross-tenant rows flagged |
| Thread membership | Message `thread_id` must match requested thread |
| Council envelope | Expert rows require `expert`, `provider`, `model`, `outcome` |
| Tier-1 transcript | HTTP 200 only after transcript persist completes; reload via `GET /api/threads/{id}` is valid |
| Tier-1 transcript fail | **503** `council_persistence_failed`; idempotency released for retry |
| Tier-2 KO | Best-effort background; KO failure does not block 200 when transcript succeeded |
| Persist observability | Failures emit `persistence_failed`; deduped success emits `persistence_recovery` |
| Retry dedupe | Same `client_request_id` does not double-append transcript/KO (in-process marker) |
| Rehydrate partial | Legacy plain assistant text tolerated; integrity codes returned when unsafe patterns detected |

### 10.3 Background persistence semantics

- **Tier-1:** `_persist_council_thread_if_needed` is **awaited** before `/council` returns HTTP 200.
- **Tier-2:** `_persist_synthesis_ko` runs in background; KO failures are logged and counted but do not change HTTP status when transcript succeeded.
- On success, `persistence_state` may be `persistence_completed` when idempotency transcript marker is set.

### 10.4 Partial persistence recovery

- **Transcript failed:** **503** `council_persistence_failed`; no idempotent success cache; retry with same `client_request_id` allowed after `fail()`.
- **KO failed, transcript ok:** HTTP 200; thread rehydrates; KO may be missing (dual-store drift — R-027).
- **Replay:** Idempotent response without re-execution; persist markers prevent duplicate append on same process.

### 10.5 Rehydration integrity

- `audit_thread_messages_for_org()` runs on thread read; safe `integrity_warnings` codes may be attached (no message bodies in warnings).
- Duplicate `council_synthesis` rows in one thread are flagged (`duplicate_council_synthesis`).

### 10.6 Verification gates

| Gate | Automated | Browser |
|------|-----------|---------|
| Chat/council roundtrip encode | pytest | NOT VERIFIED |
| Duplicate retry persist | pytest | NOT VERIFIED |
| Background fail non-blocking | pytest | NOT VERIFIED |
| Tenant isolation | pytest | NOT VERIFIED |
| Refresh partial transcript | — | NOT VERIFIED |

---

READY FOR CHATGPT REVIEW
