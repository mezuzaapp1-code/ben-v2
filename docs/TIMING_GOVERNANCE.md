# BEN Timing & Load Governance

Operational timing policy for BEN-V2. **Measure first, automate later.** This document defines targets and isolation rules; runtime enforcement is phased in after instrumentation (see `INSTRUMENTATION_PLAN.md`).

**Status:** Foundation v1 docs + **runtime timeout alignment v1** (`services/ops/timeouts.py` tier constants enforced). Council timing aligned to **single-hop rolling context** (copy-paste architecture).

---

## Core principles

| Principle | Meaning |
|-----------|---------|
| **Bounded execution** | Every user-facing path has a hard ceiling; no infinite waits. |
| **Bounded cost** | Per-request and per-tenant spend must be knowable and limitable. |
| **Bounded failure radius** | One subsystem failure must not stall or crash unrelated subsystems. |
| **Graceful degradation** | Optional layers fail without corrupting core chat/council responses. |
| **Subsystem autonomy** | Health, persistence, providers, and load governance have independent budgets. |
| **Fast-path first** | Health and liveness stay cheap; heavy work never blocks probes. |
| **Single-hop opinions** | Council is one gateway call over rolling context — no multi-model evaluation envelope. |
| **Measure first, automate later** | Instrument before queues, autoscaling, or circuit breakers. |

---

## Latency tiers (initial targets)

| Tier | Use cases | Target (p95) | Hard timeout |
|------|-----------|--------------|--------------|
| **FAST** | `/health`, `/ready`, config checks | &lt; 2s | 5s |
| **PRO** | `/chat` tier-default, single provider call | &lt; 6s | 12s |
| **DELIBERATE** | `/chat` with explicit `provider_id`; long provider tails | &lt; 20s | 25s |

**Notes**

- Targets are **design goals**, not yet enforced SLOs.
- Runtime constants: `services/ops/timeouts.py` — FAST 5s route, PRO 12s provider, DELIBERATE 25s explicit-provider chat.
- Council and ad-hoc opinions use the **same gateway** as chat; tier-default council calls use PRO (12s); streamed council uses client idle ceiling (300s) with provider HTTP bounded by stream constants.

### Council / opinion path (single-hop)

| Phase | Constant | Budget | On exceed |
|-------|----------|--------|-----------|
| **Rolling context load** | `DB_OPERATION_TIMEOUT_S` | **10s** (default) | Request fails; logged |
| **Gateway call (tier default)** | `HTTP_CLIENT_TIMEOUT_S` / `PRO_HARD_TIMEOUT_S` | **12s** | Provider error; no partial merge |
| **Gateway call (explicit provider, chat/ad-hoc)** | `CHAT_EXPLICIT_PROVIDER_TIMEOUT_S` | **25s** | Provider error surfaced to client |
| **Council stream HTTP client** | `COUNCIL_STREAM_HTTP_CLIENT_TIMEOUT_S` | **300s** | Stream abort |
| **Load governance (council)** | `BEN_MAX_CONCURRENT_COUNCIL` | **2** concurrent | 503 `council_busy` |

There is **no** per-model evaluation budget, synthesis merge step, or parallel expert wall-clock envelope.

**Visibility:** `GET /runtime/snapshot` exposes inflight counters, provider duration totals, overload rejections, and persistence failure counts.

---

## Subsystem matrix

| Subsystem | Tier | Hard timeout | Degraded fallback | Retry budget | Concurrency budget | Escalation |
|-----------|------|--------------|-------------------|--------------|-------------------|------------|
| **Health** (`/health`) | FAST | 5s | `503` + `status=degraded`, DB `fail` in checks | 0 | N/A (stateless) | Log WARNING; no alert yet |
| **Ready** (`/ready`) | FAST | 5s | `503` + `ready=false`, `migration_head=unknown` | 0 | N/A | Log WARNING |
| **Chat — tier default** | PRO | 12s | Provider error message to client | 0 | 8 (`BEN_MAX_CONCURRENT_CHAT`) | Structured WARNING |
| **Chat — explicit provider** | DELIBERATE | 25s | Provider error message to client | 0 | 8 | Structured WARNING |
| **Council / ad-hoc opinion** | PRO / DELIBERATE | 12s tier-default; 25s if explicit provider (ad-hoc) | Single response fails; no panel partials | 0 | 2 council / shared chat cap | Log WARNING |
| **Council stream (client)** | N/A | 300s idle | Client abort + retry hint | 0 | 2 council | Log lifecycle event |
| **Memory** (future) | PRO | 12s | Omit memory context; continue chat | 0 | TBD | Defer layer first |
| **Provider — OpenAI** | PRO / DELIBERATE | 12s / 25s per route | Classified provider error | 0 | Per-request | `timeout`, `config_error`, etc. |
| **Provider — Anthropic** | PRO / DELIBERATE | 12s / 25s per route | Same | 0 | Per-request | Same classification |
| **Provider — Gemini** | PRO / DELIBERATE | 12s / 25s per route | Same | 0 | Per-request | Same classification |
| **Persistence** | PRO | 10s (`DB_OPERATION_TIMEOUT_S`) | Log `persistence_failed`; stream may still complete | 0 | Per-request session | Log WARNING |
| **Background tasks** (future) | N/A | Must not block FAST/PRO/DELIBERATE | Drop or queue later | 0 until queue exists | 0 until queue | Never block user response |

**Combined inflight cap:** `BEN_MAX_TOTAL_INFLIGHT` default **12** (chat + council).

---

## Subsystem isolation model

### Rules (all subsystems)

1. **One subsystem must not stall another** — e.g. DB persist timeout must not cancel an in-flight provider response already streamed to the client.
2. **Optional layers degrade first** — diagnostics and non-critical persist before user-facing failure.
3. **Health system stays lightweight** — no provider API calls on `/health` or `/ready`; DB ping only with capped timeout.
4. **Persistence should become async later** (R-011) — today: bounded sync write on stream completion; future: enqueue after response.
5. **Council is not a separate provider stack** — same `model_gateway` and adapters as chat.

### Isolation by area

| Area | Isolation mechanism | Failure mode |
|------|---------------------|--------------|
| **Chat** | Per-call timeout; load governor | 503 overload or provider error |
| **Council** | Same gateway + load governor; rolling context load capped | 503 `council_busy` or provider error |
| **Ad-hoc expert** | Explicit `provider_id` + rolling context | 400 missing provider; 502 stream error |
| **Memory** | (Future) read timeout + skip on failure | Request without memory context |
| **Health** | Separate code path; no council imports in hot path | Degraded / not_ready |
| **Provider calls** | Per-call timeout; failure classification internal | Sanitized error, not traceback |
| **Persistence** | Bounded DB session; failures logged | `persistence_failed` event; rehydrate may gap |
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
