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

READY FOR CHATGPT REVIEW
