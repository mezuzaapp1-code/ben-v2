# BEN Project State

**Last updated:** 2026-05-19  
**Source of truth:** production-verified + `main` at `a658a1d`

---

## Production

| Item | Value |
|------|--------|
| API | `https://ben-v2-production.up.railway.app` |
| Frontend | `https://ben-v2.vercel.app` |
| Deployed commit | `a658a1d` — fix: cap Anthropic chat output and standardize provider results |
| Health | `/health` 200, DB ok (verified 2026-05-19) |
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
| Provider hardening | Live | `ProviderSendResult` dataclass; Anthropic `max_tokens` cap (default 1024); adapter call diagnostics |
| Truncation observability (chat) | Live (logs) | `truncation_detected`, `completion_truncated` in gateway logs; not API/UI |

---

## Verified systems (automated / prod smoke)

- Provider routing smoke (GPT, Claude, Gemini) — post-`a658a1d`
- Provider hardening: short GPT/Claude/Gemini; Claude long prompt (no 25s timeout); provider labels correct
- Provider transparency + thread rehydration
- Invalid `provider_id` → 400
- Language preference: `preferred_language=he` on `/chat` — passed prod smoke
- Council unchanged (`POST /council` with `question` → 200)
- Omitted `preferred_language` → prior behavior

---

## Active risks (summary)

See `docs/RISK_REGISTER.md`. Top items:

- In-memory idempotency unsafe if Railway replicas > 1
- Production auth not enforced (`ENFORCE_AUTH=false`)
- Anthropic chat tail latency (reduced by `max_tokens` cap; not eliminated under load)
- Truncation visible in logs only (`completion_truncated` not on `/chat` or UI)
- Council provider path still separate from chat adapters (12s / 512 tokens on Legal expert)
- Untracked prod smoke scripts in `scripts/` (stale SHA pins; not in repo)
- No UI for `preferred_language` (API-only)
- Auto-detect language not implemented (decision pending)

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
| GitHub `main` | `a658a1d` | Pushed |
| Railway API | Auto from `main` | On `a658a1d` |
| Vercel frontend | Auto from `main` | Deployed (toolbar + transparency bundle) |

---

## Current next task

1. **Phase cleanup:** decide untracked `scripts/prod_smoke_*.py` (commit, refresh SHA pins, or discard).
2. **Decide** auto-language detection (in scope or explicitly deferred — see `docs/CURRENT_PHASE.md`).
3. **Wire UI** `preferred_language` when product ready (no backend schema yet).
4. **Decide** replica gate: document single-replica or Postgres idempotency before scale.

---

## Local working tree (not production)

Untracked only: `scripts/probe_claude_latency_prod.py`, `scripts/prod_smoke_*.py` (several). No uncommitted provider runtime changes after `a658a1d`.
