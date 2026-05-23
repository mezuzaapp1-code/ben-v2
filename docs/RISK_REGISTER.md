# Risk Register (Operational)

**Last updated:** 2026-05-19  
**Format:** living operational register. Historical R-IDs remain in git history pre-2026-05-23.

| Risk | Severity | Owner | Mitigation | Status |
|------|----------|-------|------------|--------|
| In-memory idempotency breaks with Railway replicas > 1 | High | Platform | Document single-replica gate; Postgres idempotency before scale | Open |
| `ENFORCE_AUTH=false` in production | High | Security | Enable enforce + Clerk Bearer when ready; smoke signed paths | Open |
| Client-supplied `tenant_id` when unsigned | Medium | Auth | Tenant binding ignores/forges; signed mode uses JWT | Partial |
| Anthropic chat hits 25s on long prompts | Medium | Chat | `ANTHROPIC_CHAT_MAX_TOKENS` default 1024 (`a658a1d`); monitor tails; do not raise timeout without data | Partial |
| Truncated Claude replies invisible to user | Low | Chat | `truncation_detected` / `completion_truncated` in logs only; no API/UI yet | Partial |
| Council Legal expert 12s timeout | Medium | Council | Separate HTTP path (not chat adapters); timing data; optional tune | Open |
| Council provider path separate from chat adapters | Medium | Engineering | Dual Anthropic stacks; migrate council experts later | Open |
| Load governor per-process only | Medium | Platform | Treat snapshot as single-instance; distributed caps later | Open |
| No rate limits (token bucket) | Medium | Platform | Concurrency caps only; T-108 style limits deferred | Open |
| CORS wildcard `*.vercel.app` | Low | Security | Narrow origins in hardening phase | Open |
| Language preference API-only (no UI) | Low | Product | Wire composer when scheduled; document manual API | Open |
| Auto-detect language not decided | Low | Product | Defer or spec resolution chain before implementation | Open |
| Untracked / stale prod smoke scripts | Low | Engineering | Commit + pin `a658a1d` or discard; do not assume repo has smokes | Open |
| Gemini medium-prompt latency tail | Low | Chat | Monitor; same 25s bound as Claude | Watch |

**Severity:** High = data/security/revenue trust; Medium = reliability/UX; Low = manageable/deferred.

**Status:** Open | Partial | Watch | Accepted | Fixed

---

## Recently mitigated (production)

| Risk | Mitigation | Commit area |
|------|------------|-------------|
| Anthropic 4096 `max_tokens` causing 25s timeouts | Default cap 1024 + env override; streaming read for TTFB logs | `a658a1d` |
| Tuple `ProviderSendResult` vs tests | Frozen dataclass + `completion_truncated` flag | `a658a1d` |
| No structured adapter call logs (Anthropic) | `call_diagnostics.log_chat_provider_call` | `a658a1d` |
| Opaque `ReadTimeout("")` on chat | Provider-specific timeout messages | `fde9566` |
| No provider identity in UI | Transparency envelope + meta | `03eeca4` |
| Toolbar not wired | `provider_id` on `/chat` | `f4e62f8` |
| Council persist unbounded | 5s transcript cap | `1ebe381` |
