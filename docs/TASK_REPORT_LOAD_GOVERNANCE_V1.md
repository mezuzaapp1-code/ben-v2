# TASK REPORT

## 1. Task Name

BEN Load Governance v1

## 2. Branch

`feature/load-governance-v1`

## 3. Goal

Establish bounded runtime behavior and operational protection for `/chat` and `/council` under load: concurrency caps, duplicate council guard, structured localized overload responses, metrics hooks — without semantic caching, agents, routing changes, or tenant model changes.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `services/ops/load_governance.py` | added |
| `services/ops/load_messages.py` | added |
| `main.py` | modified |
| `frontend/src/loadGovernance.js` | added |
| `frontend/src/api/benErrors.js` | modified |
| `frontend/src/api/council.js` | modified |
| `frontend/src/App.jsx` | modified |
| `tests/test_load_governance.py` | added |
| `frontend/scripts/test-load-governance.mjs` | added |
| `docs/BEN_RUNTIME_CONTRACTS.md` | added |
| `docs/RISK_REGISTER.md` | modified |

## 5. Code Changes

### Concurrency strategy

- `LoadGovernor` uses lock-protected counters (not blocking semaphores with queues).
- `try enter` → reject immediately at cap; no task backlog.
- Defaults: chat=8, council=2, total inflight=12 (env-overridable).

### Overload semantics

| Code | HTTP | Meaning |
|------|------|---------|
| `runtime_saturated` | 503 | Chat slots full |
| `council_busy` | 503 | Council slots full |
| `duplicate_request` | 429 | Same tenant+question in flight |
| `retry_later` | 503 | Total inflight cap |

Localized `message` + `hint` in en/he/ar.

### Bounded execution guarantees

- Council/chat handlers run inside `async with govern_*` — slots released in `finally`.
- Duplicate keys expire after `BEN_COUNCIL_DEDUP_WINDOW_S` (default 45s).
- Frontend: `canSubmitCouncil` blocks in-flight + 2.5s rapid duplicate clicks; `markCouncilSubmitFinished` in council `finally` with `setLoading(false)`.

### Metrics hooks

Structured logs on active/completed/rejected with `active_chat_requests`, `active_council_requests`, `rejected_overload_requests`, `council_duration_ms`.

## 6. Verification Executed

```bash
cd c:\BEN-V2
python -m pytest tests/test_load_governance.py -q
cd frontend
npm run build
node scripts/test-load-governance.mjs
```

Browser matrix (spam click, parallel council, refresh during council, timeout recovery): **NOT EXECUTED**.

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| Governor unit tests (duplicate, saturate) | **PASS** | 7 pytest |
| API structured overload | **PASS** | 503 `council_busy`, 429 `duplicate_request` |
| Hebrew overload strings | **PASS** | `test_overload_detail_hebrew` |
| Client guard smoke | **PASS** | `test-load-governance.mjs` |
| `npm run build` | **PASS** | Vite production build |
| Browser verification | **NOT VERIFIED** | Required for FIXED |

## 8. Remaining Scale Risks

- Per-process limits only (R-037); multiple Railway workers multiply caps.
- No per-tenant fairness or token-bucket rate limits (R-015 still PARTIAL).
- Provider quotas unchanged — governance protects BEN process, not OpenAI/Anthropic/Google accounts.
- No distributed dedup across instances.

## 9. Future Governance Layers

- Redis-backed semaphores + dedup for multi-worker.
- Per-tenant concurrency budgets.
- Priority tiers (pro vs free) without changing provider routing.
- Queue with max depth + SLA (T-107) when product requires queued council.

## 10. Risks Updated

- **R-010** PARTIAL — load isolation in-process
- **R-015** PARTIAL — governance vs rate limiting
- **R-036** PARTIAL — overload/provider saturation
- **R-037** OPEN — multi-worker bounds

---

READY FOR CHATGPT REVIEW
