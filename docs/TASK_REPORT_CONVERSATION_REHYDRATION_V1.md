# TASK REPORT — Conversation Rehydration v1

## 1. Task Name

Persistent threads: backend continuity, read APIs, council transcript persistence, frontend reload.

## 2. Branch

`feature/conversation-rehydration-v1` (not merged)

## 3. Goal

Conversations survive browser refresh; sidebar backed by server threads; follow-up `/chat` and `/council` append to same thread.

## 4. Before / after architecture

**Before**

- Frontend: `useState` only; refresh cleared UI.
- `POST /chat`: no `thread_id`; new DB thread every call.
- No list/read HTTP APIs.
- Council: HTTP response only; synthesis in `knowledge_objects` (not thread messages).

**After**

- `POST /chat` / `POST /council`: optional `thread_id`; append or create via `resolve_thread_id`.
- `GET /api/threads`, `GET /api/threads/{id}`: tenant-scoped (JWT / `BEN_ANONYMOUS_ORG_ID`).
- Messages: plain user text; assistant/council via JSON envelope in `content` (`services/message_format.py`).
- Council: persists user question + expert rows + synthesis to thread (creates thread if none).
- Frontend: `localStorage` active thread; load list + messages on mount; sends `thread_id` when persisted.

## 5. Endpoints added

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/threads` | List recent threads (limit 50) |
| GET | `/api/threads/{thread_id}` | Thread metadata + ordered messages |

**Extended (body fields)**

| Method | Path | Field |
|--------|------|-------|
| POST | `/chat` | optional `thread_id` |
| POST | `/council` | optional `thread_id` |

Council HTTP response shape unchanged (`cost_usd`, `council`, `question`, `request_id`, `synthesis`).

## 6. Files changed

| File | Change |
|------|--------|
| `services/message_format.py` | added |
| `services/thread_service.py` | added |
| `services/chat_service.py` | modified |
| `services/council_service.py` | modified |
| `main.py` | modified |
| `frontend/src/threadStorage.js` | added |
| `frontend/src/api/threads.js` | added |
| `frontend/src/App.jsx` | modified |
| `frontend/src/App.css` | modified |
| `tests/test_conversation_rehydration.py` | added |
| `tests/test_council_*.py`, `test_reasoning_preservation.py`, `test_tenant_binding.py` | mock council thread persist |
| `docs/RISK_REGISTER.md` | R-026, R-027 |
| `docs/STATUS_REPORT.md` | updated |

## 7. Verification executed

```bash
python -m pytest tests/test_conversation_rehydration.py tests/test_tenant_binding.py tests/test_council_degraded_honesty.py tests/test_council_gemini_strategy.py tests/test_reasoning_preservation.py -v
cd frontend && npm run build
```

Browser refresh / prod: **NOT EXECUTED** in agent session (requires manual or E2E).

## 8. Verification results

| Check | Result |
|-------|--------|
| Message encode/decode roundtrip | **PASS** |
| `thread_id` wired to chat/council handlers | **PASS** |
| List/get thread API (mocked) | **PASS** |
| Council + tenant binding tests | **PASS** |
| Frontend build | **PASS** (run locally) |
| Manual refresh rehydration | **NOT VERIFIED** |

### VERIFIED vs INFERRED

| Finding | Class |
|---------|--------|
| APIs + encoding in pytest | **VERIFIED** |
| Refresh restores UI in prod | **INFERRED** until manual test |

## 9. Remaining limitations

- Legacy rows: plain-text assistant messages still render; no retroactive council metadata.
- `knowledge_objects` synthesis rows remain separate from thread transcript.
- Council without client `thread_id` still creates a server thread (for persist); UI links via “latest thread” list fetch after council on draft.
- No pagination on thread list; cap 50.
- No `model_responses` table (never existed).

## 10. Risks

| ID | Status |
|----|--------|
| R-026 | **PARTIAL** — implemented; refresh E2E not verified |
| R-027 | **PARTIAL** — council in-thread persist; KO duplicate; draft→thread heuristics |

## 11. Readiness

**READY FOR REVIEW** — merge after manual refresh test on staging/prod.

---

READY FOR CHATGPT REVIEW
