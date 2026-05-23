# Deferred Work

**Last updated:** 2026-05-23

Ideas **intentionally not built** yet. Do not implement without updating `docs/ROADMAP.md` and `docs/CURRENT_PHASE.md`.

| Item | Reason deferred | Revisit when |
|------|-----------------|--------------|
| Cognitive memory / KO graph UX | Scope; trust model needs provider clarity first | Provider + language stable 2+ weeks |
| Hat registry / persistent governance | No product spec; risk of hidden autonomy | Governance design doc approved |
| User-level `preferred_language` (DB) | v1 is request-only; no migration appetite | UI settings phase scheduled |
| Thread-level `preferred_language` | Same as above | After user prefs |
| Auto-detect language resolution | Complexity; explicit `en`/`he` sufficient for v1 | User prefs shipped + user feedback |
| Council on provider adapters | Council uses system prompts; large test surface | Chat adapters proven + bandwidth |
| True SSE chat streaming | Non-streaming path stable | Product asks for token stream |
| Provider `system` role (vs prepend) | Prepend works for v1 | Adapter phase 2 |
| Postgres idempotency | In-process OK at 1 replica | Before Railway replicas > 1 |
| Distributed load semaphores | Per-process caps enough for now | Multi-replica + metrics show saturation |
| Token-bucket rate limits | Concurrency governance partial | Abuse or cost incident |
| `ENFORCE_AUTH=true` default | Prod smoke uses unsigned | Clerk E2E matrix green |
| Multi-provider deliberation from toolbar | Council fixed 3-expert pipeline | New product spec |
| “+ Add AI” dynamic provider registry | Only 3 providers configured | Provider onboarding process exists |
| Engineering OS automation (T-104) | Manual ops sufficient | Team pain justifies build |
| Dynamic provider config (T-106) | Env vars + registry enough | Frequent model changes without deploy |
| UI `completion_truncated` badge | Backend observability first | Truncation metrics show user impact |
| Input char cap on `/chat` | Language + timeout partial mitigations | Prod shows pathological prompts |
| Arabic (`ar`) language code | `en`/`he` only for v1 | Product priority |
