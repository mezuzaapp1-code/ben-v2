# TASK REPORT — Council request lifecycle recoverable

## 1. Root cause

| Layer | Issue |
|-------|--------|
| **Backend** | `await _persist_council_thread_if_needed` and `await _persist_synthesis_ko` ran **before** returning the HTTP response. Slow or failing DB work delayed or blocked the client after experts/synthesis completed. |
| **Frontend** | No `res.ok` check — 401/400/422 bodies rendered as empty council or raw JSON in bubbles. |
| **Frontend** | No in-thread progress UI during 12–25s wait — looked like “no response”. |
| **Frontend** | No `AbortController` — browser could wait indefinitely past server cap. |
| **Rehydration** | Post-council `fetchThreadList` was awaited before UI update on draft threads, adding latency (secondary). |

## 2. Lifecycle trace (after fix)

1. User clicks **Council** → `council_submit_started` (dev log).
2. User message appended; `councilStatus` = “Council started…” within **0ms** (phase timers at 0/300ms/12s).
3. `buildBenHeaders` → `council_request_sent` (hasAuth, hasThreadId).
4. `POST /council` with `AbortController` **35s** cap, optional `thread_id`.
5. Backend: experts + synthesis under **25s** outer cap → payload built → **background** persist → HTTP **200**.
6. `council_response_received` → parse → render bubbles or humanized error → `council_render_completed`.
7. `finally`: clear timers, `loading=false`, `council_submit_finally`.

## 3. Files changed

| File | Change |
|------|--------|
| `services/council_service.py` | `_schedule_background_task`; persist/KO after response |
| `frontend/src/api/council.js` | fetch, parse, humanize errors |
| `frontend/src/councilLifecycleLog.js` | dev-only lifecycle logs |
| `frontend/src/App.jsx` | council handler, progress UI, abort |
| `frontend/src/App.css` | `.council-progress`, `.council-error` |
| `tests/test_council_lifecycle.py` | added |
| `docs/RISK_REGISTER.md` | R-028 |

## 4. Before / after

| Before | After |
|--------|--------|
| DB persist could block JSON response | Response returns immediately; persist in background |
| HTTP errors → empty or raw JSON | Human-readable `council_error` bubble |
| Long wait with no feedback | Progress line within 300ms |
| No client timeout | 35s abort → “Council timed out. You can retry.” |
| `loading` stuck if unhandled | `finally` always clears |

## 5. Verification

```bash
python -m pytest tests/test_council_lifecycle.py tests/test_conversation_rehydration.py tests/test_council_degraded_honesty.py -v
cd frontend && npm run build
```

| Check | Result |
|-------|--------|
| Pytest | **PASS** (13+) |
| Frontend build | **PASS** |
| Browser manual (short/long/abort/refresh) | **NOT VERIFIED** |

## 6. Risks

**R-028** — **PARTIAL** until browser verification.

---

READY FOR CHATGPT REVIEW
