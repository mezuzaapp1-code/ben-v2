# BEN Roadmap

**Last updated:** 2026-05-19

Phase sequence for BEN-V2. **Current phase** marked explicitly.

---

## Phase sequence

| # | Phase | Status |
|---|--------|--------|
| 0 | Stabilization (health, tenant, persist, idempotency) | Done |
| 1 | Council honesty + durability + room metadata | Done |
| 2 | **Provider-first chat stabilization** | **Current** (cleanup in progress) |
| 3 | Provider hardening (truncation, max_tokens, diagnostics) | Done (`a658a1d`) |
| 4 | Language persistence (user/thread + UI) | Planned |
| 5 | Council ↔ adapter convergence (optional) | Later |
| 6 | Governance / hat registry v1 | Later |
| 7 | Cognitive memory v1 | Later |
| 8 | Scale gates (Postgres idempotency, replicas) | Later |

---

## Current phase: Provider-first chat stabilization

**Goal:** Toolbar → routed provider → visible identity → bounded errors → observable provider calls.

**Delivered to production (`a658a1d` baseline):**

- Provider toolbar + `provider_id` routing (`f4e62f8`)
- Provider adapters (chat) (`9864909`)
- Provider transparency (API + UI + persist) (`03eeca4`)
- Timeout messages + 25s explicit provider budget (`fde9566`)
- Request-level `preferred_language` (`en`, `he`) (`0d81665`)
- Provider hardening: Anthropic `max_tokens` cap, `ProviderSendResult` dataclass, adapter diagnostics, truncation logging (`a658a1d`)

**Remaining before phase exit (stabilization cleanup):**

- Decision on untracked `scripts/prod_smoke_*.py` (commit, maintain, or discard)
- Decision on auto-language detection (defer vs spec)
- Replica/idempotency gate documented
- Choose next phase (language persistence vs governance vs memory)

**Do not start Phase 4 implementation until exit criteria in `docs/CURRENT_PHASE.md` are met.**

---

## Completed: Provider hardening (`a658a1d`)

- `ANTHROPIC_CHAT_MAX_TOKENS` default 1024 (env-tunable)
- Structured adapter logging (`ttfb_ms`, `truncation_detected`, token fields)
- `completion_truncated` on gateway dict (logs; not `/chat` API)
- Prod smoke: GPT/Claude/Gemini short; Claude long prompt once; `preferred_language=he`; council unchanged

**Optional follow-up (same phase or Phase 4):** expose `completion_truncated` in API (not UI).

---

## Phase 4: Language persistence

- `user_preferences.preferred_language`
- `threads.preferred_language` override
- UI: account + thread selector
- Resolution order (explicit message > request > thread > user > default)
- Auto-detect only if explicitly approved in phase gate

**Exit:** User sets Hebrew once; all threads default Hebrew without per-request field.

---

## Later phases (ordered tentatively)

1. **Council adapter migration** — reduce duplicate HTTP; keep expert personas in council only  
2. **Governance / hats** — policies without recursive agents  
3. **Memory v1** — thread-scoped recall; no autonomous writes  
4. **Scale** — Postgres idempotency, distributed caps, `ENFORCE_AUTH=true`  
5. **Streaming chat** — SSE per provider, same transparency rules  

---

## Out of roadmap (unless reprioritized)

- Autonomous BEN persona chat
- Unlimited agent swarms
- Full i18n of codebase/logs

---

## How to reprioritize

Update this file + `docs/CURRENT_PHASE.md` + `docs/PROJECT_STATE.md` in the same commit. Do not start implementation without phase alignment.
