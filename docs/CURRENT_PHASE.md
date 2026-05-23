# Current Phase

**Phase name:** Provider-First Chat Stabilization  
**Last updated:** 2026-05-23

---

## Phase goal

Make BEN a **provider-first cognitive workspace**: users choose GPT, Claude, or Gemini; responses show **which engine spoke**; chat is **bounded and observable**; council remains a separate deliberation path.

---

## Allowed scope

- `/chat` provider routing, adapters, timeouts, transparency
- Request-level `preferred_language` (`en`, `he`)
- Structured logging for provider calls (English only)
- Minimal frontend: toolbar, provider meta line, composer
- Tests and production smoke for above
- Operational docs (`docs/PROJECT_STATE.md`, etc.)
- Bug fixes that preserve council/synthesis contracts

---

## Forbidden scope

- Council orchestration redesign
- Synthesis prompt/JSON schema changes (unless explicit honesty bug)
- Cognitive memory / KO graph productization
- Hat registry / persistent governance layer
- User/thread language DB persistence (next phase)
- Auto-detect language resolution chain
- BEN persona masking providers
- Multi-replica idempotency without design gate
- Large UI redesign or settings screens (unless explicitly scheduled)

---

## Exit criteria

- [x] Toolbar → `provider_id` on `/chat` in production
- [x] Provider identity visible in UI and rehydration
- [x] Explicit-provider timeout 25s with clear error messages
- [x] Request-level language preference live (`en`/`he`)
- [ ] Uncommitted truncation / `max_tokens` work shipped or explicitly deferred
- [ ] Documented replica/idempotency gate before multi-instance Railway
- [ ] Next phase chosen in `docs/ROADMAP.md` (language persistence vs governance vs memory)

---

## How to start a session

1. Read `docs/PROJECT_STATE.md`
2. Read this file
3. Read `docs/SYSTEM_BOUNDARIES.md` before touching a layer
4. Confirm production commit on `/health` before prod smoke
