# BEN Project State

**Last updated:** 2026-06-06  
**Source of truth:** codebase + pre-expansion cleanup (tasks 001–002 complete)

---

## Production

| Item | Value |
|------|--------|
| API | `https://ben-v2-production.up.railway.app` |
| Frontend | `https://ben-v2.vercel.app` |
| Auth mode | `ENFORCE_AUTH=false` (unsigned chat/council allowed) |
| Council architecture | **Rolling context / copy-paste** — single `model_gateway` call per request |

---

## Completed layers (verified)

| Layer | Status | Notes |
|-------|--------|-------|
| Tenant binding | Live | JWT org / anonymous org; body `tenant_id` not trusted when signed |
| Thread + message persist | Live | RLS via `app.current_org_id` |
| Conversation rehydration | Live | `/api/threads`, message JSON envelopes |
| Idempotency (in-process) | Live | Chat + council replay |
| Load governance | Live | Chat/council concurrency caps, overload codes |
| Runtime diagnostics | Live | Request lifecycle, provider counters, snapshot |
| Council (rolling context) | Live | `copy_paste_service` → `rolling_context` → `model_gateway`; `mode: "copy_paste"` |
| Ad-hoc expert opinions | Live | Per-provider rolling context via `/adhoc/expert[/stream]` |
| Provider toolbar (UI) | Live | GPT / Claude / Gemini selection on chat + ad-hoc |
| Provider routing (`/chat`) | Live | `provider_id` → single gateway provider |
| Provider adapters (chat) | Live | OpenAI, Anthropic, Gemini HTTP adapters |
| Provider transparency | Live | `provider_id` + model in API, persist, UI meta |
| Chat timeout governance | Live | 25s explicit provider; 12s tier-default |
| Language preference v1 | Live | Request `preferred_language`: `en`, `he` only |
| Provider hardening | Live | `ProviderSendResult` dataclass; Anthropic `max_tokens` cap; adapter diagnostics |
| Dead code purge (001) | Complete | Orphaned council modules + synthesis route removed |
| Frontend cleanup (002) | Complete | Synthesis button, phase timers, legacy stream handlers removed |

---

## Removed / no longer live

| Item | Status |
|------|--------|
| Parallel expert council panel | **Removed** |
| Council JSON synthesis merge step | **Removed** |
| Ad-hoc synthesis endpoint (`POST .../adhoc/synthesize`) | **Removed** (404) |
| `services/council_room.py` and related modules | **Deleted** |
| Council expert-phase UI timers | **Removed** from frontend |

---

## Verified systems (automated / prod smoke)

- Provider routing smoke (GPT, Claude, Gemini)
- Provider transparency + thread rehydration
- Invalid `provider_id` → 400
- Language preference: `preferred_language=he` on `/chat`
- `POST /council` returns `mode: "copy_paste"` with markdown `response`
- FastAPI import clean after backend purge
- Frontend `npm run build` clean after UI purge

---

## Active risks (summary)

See `docs/RISK_REGISTER.md`. Top items:

- In-memory idempotency unsafe if Railway replicas > 1
- Production auth not enforced (`ENFORCE_AUTH=false`)
- Anthropic chat tail latency under load
- Truncation visible in logs only (not on `/chat` or UI)
- Legacy council tests still reference removed architecture (cleanup pending)
- No UI for `preferred_language` (API-only)
- Auto-detect language not implemented (decision pending)

---

## Not implemented (confirmed)

- Persistent governance / hat registry
- Cognitive memory layer
- User/thread language preferences (DB)
- Postgres-backed idempotency
- True SSE streaming for chat
- Project / member / task / ledger models (Task `004`)
- Auth enforcement on project routes (Task `005`)

---

## Current next task

1. **Task `004`:** Establish Project, Member, Task & Ledger models + Alembic migration.
2. **Task `005`:** Harden project routes with forced auth.
3. **Test suite:** Rewrite or remove legacy council tests that reference the old panel architecture.
4. **Decide** replica gate: document single-replica or Postgres idempotency before scale.

---

## Local working tree

Tasks 001–003 documentation alignment in progress. See `.tasks/README.md` for master task board.
