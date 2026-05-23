# BEN Roadmap

**Last updated:** 2026-05-23

Phase sequence for BEN-V2. **Current phase** marked explicitly.

---

## Phase sequence

| # | Phase | Status |
|---|--------|--------|
| 0 | Stabilization (health, tenant, persist, idempotency) | Done |
| 1 | Council honesty + durability + room metadata | Done |
| 2 | **Provider-first chat stabilization** | **Current** |
| 3 | Provider hardening (truncation, max_tokens, diagnostics ship) | Next |
| 4 | Language persistence (user/thread + UI) | Planned |
| 5 | Council ↔ adapter convergence (optional) | Later |
| 6 | Governance / hat registry v1 | Later |
| 7 | Cognitive memory v1 | Later |
| 8 | Scale gates (Postgres idempotency, replicas) | Later |

---

## Current phase: Provider-first chat stabilization

**Goal:** Toolbar → routed provider → visible identity → bounded errors.

**Delivered to production (`0d81665`):**

- Provider toolbar + `provider_id` routing
- Provider adapters (chat)
- Provider transparency (API + UI + persist)
- Timeout messages + 25s explicit provider budget
- Request-level `preferred_language` (`en`, `he`)

**Remaining in phase:**

- Ship uncommitted truncation / `max_tokens` / call diagnostics
- Replica/idempotency decision documented

---

## Next phase: Provider hardening

- Commit truncation observability + `ANTHROPIC_CHAT_MAX_TOKENS`
- Production verify long-prompt Claude
- Optional: expose `completion_truncated` in API (not UI)
- Stale comment cleanup in frontend registry

**Exit:** 7 days prod without silent truncation complaints; Claude p95 < 20s on typical prompts.

---

## Phase 4: Language persistence

- `user_preferences.preferred_language`
- `threads.preferred_language` override
- UI: account + thread selector
- Resolution order (explicit message > request > thread > user > default)
- Still no auto-detect unless specified

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
