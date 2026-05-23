# BEN Project State

**Last updated:** 2026-05-23  
**Source of truth:** production-verified + `main` at `0d81665`

---

## Production

| Item | Value |
|------|--------|
| API | `https://ben-v2-production.up.railway.app` |
| Frontend | `https://ben-v2.vercel.app` |
| Deployed commit | `0d81665` — feat: add request-level chat language preference |
| Health | `/health` 200, DB ok (verified on deploy) |
| Auth mode | `ENFORCE_AUTH=false` (unsigned chat/council allowed) |

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
| Council (3 experts + synthesis) | Live | Unchanged architecture; room metadata |
| Council transcript durability | Live | Tier-1 persist before 200; bounded 5s |
| Provider toolbar (UI) | Live | GPT / Claude / Gemini selection |
| Provider routing (`/chat`) | Live | `provider_id` → single gateway provider |
| Provider adapters (chat) | Live | OpenAI, Anthropic, Gemini HTTP adapters |
| Provider transparency | Live | `provider_id` + model in API, persist, UI meta |
| Chat timeout governance | Live | 25s explicit provider; 12s tier-default |
| Language preference v1 | Live | Request `preferred_language`: `en`, `he` only |

---

## Verified systems (automated / prod smoke)

- Provider routing smoke (GPT, Claude, Gemini)
- Provider transparency + thread rehydration
- Invalid `provider_id` → 400
- Language preference: `he`/`en` instruction to gateway; raw user message in DB
- Council unchanged; `preferred_language` on `/council` → 422
- Omitted `preferred_language` → prior behavior

---

## Active risks (summary)

See `docs/RISK_REGISTER.md`. Top items:

- In-memory idempotency unsafe if Railway replicas > 1
- Production auth not enforced (`ENFORCE_AUTH=false`)
- Anthropic chat tail latency near 25s on long prompts
- Uncommitted local work: truncation observability, extended adapter diagnostics
- Council Legal (Anthropic) 12s expert timeout under load
- No UI for `preferred_language` (API-only)

---

## Not implemented (confirmed)

- Persistent governance / hat registry
- Cognitive memory layer
- User/thread language preferences (DB)
- Postgres-backed idempotency
- True SSE streaming for chat
- Council on provider adapters

---

## Deployment status

| Target | Branch | Status |
|--------|--------|--------|
| GitHub `main` | `0d81665` | Pushed |
| Railway API | Auto from `main` | On `0d81665` |
| Vercel frontend | Auto from `main` | Deployed (toolbar + transparency bundle) |

---

## Current next task

1. **Commit and ship** uncommitted provider work (truncation observability, `max_tokens` cap, call diagnostics) — separate from language commit.
2. **Wire UI** `preferred_language` when product ready (no backend schema yet).
3. **Decide** replica gate: document single-replica or Postgres idempotency before scale.

---

## Local working tree (not production)

Uncommitted changes exist for: `model_gateway`, Anthropic adapter, `ProviderSendResult`, truncation tests. Do not treat as deployed until committed and smoke-verified.
