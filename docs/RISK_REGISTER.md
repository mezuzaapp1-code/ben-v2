# Risk Register (Operational)

**Last updated:** 2026-05-23  
**Format:** living operational register. Historical R-IDs remain in git history pre-2026-05-23.

| Risk | Severity | Owner | Mitigation | Status |
|------|----------|-------|------------|--------|
| In-memory idempotency breaks with Railway replicas > 1 | High | Platform | Document single-replica gate; Postgres idempotency before scale | Open |
| `ENFORCE_AUTH=false` in production | High | Security | Enable enforce + Clerk Bearer when ready; smoke signed paths | Open |
| Client-supplied `tenant_id` when unsigned | Medium | Auth | Tenant binding ignores/forges; signed mode uses JWT | Partial |
| Anthropic chat hits 25s on long prompts / high `max_tokens` | Medium | Chat | `ANTHROPIC_CHAT_MAX_TOKENS=1024` (local, uncommitted); prompt bounds | Partial |
| Truncated Claude replies invisible to user | Low | Chat | `truncation_detected` logging (local, uncommitted); future UI flag | Partial |
| Council Legal expert 12s timeout | Medium | Council | Timing data; optional model/timeout tune; not chat path | Open |
| Load governor per-process only | Medium | Platform | Treat snapshot as single-instance; distributed caps later | Open |
| No rate limits (token bucket) | Medium | Platform | Concurrency caps only; T-108 style limits deferred | Open |
| CORS wildcard `*.vercel.app` | Low | Security | Narrow origins in hardening phase | Open |
| Language preference API-only (no UI) | Low | Product | Wire composer when scheduled; document manual API | Open |
| Dual Anthropic HTTP stacks (chat adapter vs council) | Medium | Engineering | Migrate council experts to adapters later | Open |
| Uncommitted adapter/diagnostic work in working tree | Low | Engineering | Commit or discard before next prod assumption | Open |
| Gemini medium-prompt latency tail | Low | Chat | Monitor; same 25s bound as Claude | Watch |

**Severity:** High = data/security/revenue trust; Medium = reliability/UX; Low = manageable/deferred.

**Status:** Open | Partial | Watch | Accepted | Fixed

---

## Recently mitigated (production)

| Risk | Mitigation | Commit area |
|------|------------|-------------|
| Opaque `ReadTimeout("")` on chat | Provider-specific timeout messages | `fde9566` |
| No provider identity in UI | Transparency envelope + meta | `03eeca4` |
| Toolbar not wired | `provider_id` on `/chat` | `f4e62f8` |
| Council persist unbounded | 5s transcript cap | `1ebe381` |
