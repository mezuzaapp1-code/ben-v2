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

READY FOR CHATGPT REVIEW
