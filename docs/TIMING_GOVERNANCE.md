# BEN Timing & Load Governance

Operational timing policy for BEN-V2. **Measure first, automate later.** This document defines targets and isolation rules; runtime enforcement is phased in after instrumentation (see `INSTRUMENTATION_PLAN.md`).

**Status:** Foundation v1 docs + **runtime timeout alignment v1** (`services/ops/timeouts.py` tier constants enforced).

---

## Core principles

| Principle | Meaning |
|-----------|---------|
| **Bounded execution** | Every user-facing path has a hard ceiling; no infinite waits. |
| **Bounded cost** | Per-request and per-tenant spend must be knowable and limitable. |
| **Bounded failure radius** | One subsystem failure must not stall or crash unrelated subsystems. |
| **Graceful degradation** | Optional layers fail open to partial success (experts without synthesis, degraded expert text). |
| **Subsystem autonomy** | Council, synthesis, health, persistence, and providers have independent budgets. |
| **Fast-path first** | Health and liveness stay cheap; heavy work never blocks probes. |
| **Partial success allowed** | 3/3 experts not required for HTTP 200; synthesis optional by design. |
| **Measure first, automate later** | Instrument before queues, autoscaling, or circuit breakers. |

---

## Latency tiers (initial targets)

| Tier | Use cases | Target (p95) | Hard timeout |
|------|-----------|--------------|--------------|
| **FAST** | `/health`, `/ready`, config checks | &lt; 2s | 5s |
| **PRO** | `/chat` (free tier), single provider call | &lt; 6s | 12s |
| **DELIBERATE** | `POST /council` (3 experts + synthesis + optional persist) | &lt; 15s | 25s |

**Notes**

- Targets are **design goals**, not yet enforced SLOs.
- Runtime constants: `services/ops/timeouts.py` — FAST 5s route, PRO 12s provider, synthesis 10s, Tier-1 transcript persist 5s, DB ping 2s; experts run in parallel within DELIBERATE envelope.
- User-facing responses must return within **DELIBERATE** hard timeout even if synthesis or persist is skipped.
- **R-017:** `run_council` wrapped in `asyncio.wait_for(..., COUNCIL_TOTAL_TIMEOUT_S)` (25s); partial expert results preserved on outer timeout; persist skipped if envelope exceeded.

### Council room budget (25s envelope)

Sub-budgets are **not** additive (experts run in parallel); they reserve wall-clock within the single room cap:

| Phase | Constant | Budget | On exceed |
|-------|----------|--------|-----------|
| **Experts (parallel)** | `EXPERT_CALL_TIMEOUT_S` | **12s** each (wall ≈ max of 3) | Per-expert degraded `timeout` / `error` |
| **Synthesis** | `SYNTHESIS_TIMEOUT_S` | **10s** | `synthesis: null`; room degraded |
| **Tier-1 transcript** | `DB_OPERATION_TIMEOUT_S` | **5s** | **503** `council_persistence_failed`; idempotency released |
| **Tier-2 KO** | `DB_OPERATION_TIMEOUT_S` | **5s** (background) | Log only; HTTP 200 if transcript OK |
| **Total room** | `COUNCIL_TOTAL_TIMEOUT_S` | **25s** | Partial payload; `council_room_budget_pressure` metric |

**Visibility:** `GET /runtime/snapshot` exposes `council_room_budget_envelope_ms`, `council_room_budget_pressure_count`, `transcript_persist_timeout_count`, per-expert timeout counts, and expert latency p95 (rolling sample).

---

## Subsystem matrix

For each subsystem: target latency (tier), hard timeout, degraded fallback, retry budget, concurrency budget, escalation policy.

| Subsystem | Tier | Hard timeout | Degraded fallback | Retry budget | Concurrency budget | Escalation |
|-----------|------|--------------|-------------------|--------------|-------------------|------------|
| **Health** (`/health`) | FAST | 5s | `503` + `status=degraded`, DB `fail` in checks | 0 | N/A (stateless) | Log WARNING; no alert yet |
| **Ready** (`/ready`) | FAST | 5s | `503` + `ready=false`, `migration_head=unknown` | 0 | N/A | Log WARNING |
| **Council — experts (parallel)** | DELIBERATE | 12s per expert (within 25s total) | `Expert unavailable ({category})` per expert | 0 per expert | 3 parallel calls max | Structured WARNING; no user 5xx |
| **Council — synthesis** | DELIBERATE | 10s (current `SYNTHESIS_TIMEOUT_S`) | `synthesis: null`; experts unchanged | 0 | 1 per request | Log WARNING; skip persist if no synthesis |
| **Council — Tier-1 transcript** | DELIBERATE | 5s (`DB_OPERATION_TIMEOUT_S`) | **503** persistence failed; no success cache | 0 | 1 per council | Log WARNING + `transcript_persist_timeout_count` |
| **Council — Tier-2 KO** | DELIBERATE | 5s (`DB_OPERATION_TIMEOUT_S`) | Skip KO write; council response still 200 | 0 | 1 per successful synthesis | Log WARNING |
| **Memory** (future) | PRO | 12s | Omit memory context; continue chat/council | 0 | TBD | Defer layer first |
| **Provider — OpenAI** | PRO / DELIBERATE | **12s** (`HTTP_CLIENT_TIMEOUT_S` / `EXPERT_CALL_TIMEOUT_S`) | Degraded expert or null synthesis | 0 | Per-request | Classify: `timeout`, `config_error`, etc. |
| **Provider — Anthropic** | PRO / DELIBERATE | **12s** (same) | Degraded legal expert | 0 | Per-request | Same classification |
| **Background tasks** (future) | N/A | Must not block FAST/PRO/DELIBERATE | Drop or queue later | 0 until queue exists | 0 until queue | Never block user response |

---

## Subsystem isolation model

### Rules (all subsystems)

1. **One subsystem must not stall another** — e.g. synthesis timeout must not cancel completed expert results.
2. **Optional layers degrade first** — synthesis and persistence before expert retries or user-facing failure.
3. **Health system stays lightweight** — no provider API calls on `/health` or `/ready`; DB ping only with 2s cap.
4. **Persistence should become async later** (R-011) — today: bounded sync write; future: enqueue after response.
5. **Synthesis failure remains isolated** — already implemented; governance codifies it.

### Isolation by area

| Area | Isolation mechanism | Failure mode |
|------|---------------------|--------------|
| **Council** | `asyncio.gather` → per-expert `_safe_expert`; exceptions contained | Partial council array |
| **Synthesis** | `try/except` + `wait_for`; separate from expert gather | `synthesis: null` |
| **Memory** | (Future) read timeout + skip on failure | Request without memory context |
| **Health** | Separate code path; no council imports in hot path | Degraded / not_ready |
| **Provider calls** | Per-call timeout; failure classification internal | Degraded string, not traceback |
| **Persistence** | Tier-1: `wait_for` on transcript; Tier-2: `wait_for` on KO | Tier-1 fail → 503; KO fail → HTTP 200 if transcript OK |
| **Background tasks** | (Future) fire-and-forget queue with own budget | Never on critical path |

---

## Operational timing rules

1. **No unbounded waits** — every external I/O has an explicit timeout.
2. **Every provider call** must use centralized timeout constants (`services/ops/timeouts.py`) and be reviewed against tiers above.
3. **Every async task** must have a cancellation strategy (`asyncio.wait_for` or httpx timeout).
4. **Retries must be bounded** — default **0** retries on provider calls until retry policy is documented and instrumented.
5. **Background work must not block user response** — persist-to-DB eventually moves off the hot path.

---

## Future implementation phases

| Phase | Deliverable | Depends on |
|-------|-------------|------------|
| v1 (this doc) | Governance + targets | — |
| v2 | Latency metrics in logs/responses | R-012, instrumentation |
| v3 | Request-level budget enforcement | v2 |
| v4 | Queue for persistence / heavy work | R-011 |
| v5 | Tenant cost ceilings | `COST_GOVERNANCE.md` |

---

READY FOR CHATGPT REVIEW
