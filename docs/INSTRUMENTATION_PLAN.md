# BEN Instrumentation Plan

Foundation for **measurable** operations. This phase defines what to measure and why; implementation (metrics export, alerts, dashboards) is **future work**.

**Principle:** Measure first, automate later (`TIMING_GOVERNANCE.md`).

---

## Latency metrics

| Metric | What | Why | Future alert (example) | Future SLO (example) |
|--------|------|-----|------------------------|----------------------|
| `provider_latency_ms` | OpenAI / Anthropic round-trip per call | Detect slow or failing providers | p95 &gt; 8s for 5 min | p95 &lt; 6s per expert |
| `synthesis_latency_ms` | OpenAI synthesis completion | Isolate synthesis regressions | p95 &gt; 10s | p95 &lt; 8s |
| `db_latency_ms` | Ping, read, write (KO persist) | DB saturation or network | ping p95 &gt; 1s | ping p95 &lt; 500ms |
| `health_latency_ms` | `/health`, `/ready` total | Keep probes FAST tier | p95 &gt; 2s | p95 &lt; 1s |
| `council_total_latency_ms` | `POST /council` end-to-end | User-facing DELIBERATE tier | p95 &gt; 20s | p95 &lt; 15s |

**Labels (future):** `request_id`, `subsystem`, `provider`, `tenant_id` (hashed), `tier`.

---

## Load metrics

| Metric | What | Why | Future alert (example) |
|--------|------|-----|------------------------|
| `concurrent_requests` | In-flight HTTP requests | Capacity planning | &gt; N for 5 min |
| `queue_depth` | (Future) background job queue | Backpressure signal | depth &gt; 100 |
| `retry_count` | Provider retries per request | Retry storms | &gt; 0 sustained (when retries enabled) |
| `timeout_count` | Timeouts by subsystem/category | Tune budgets | &gt; 5% of council requests |

---

## Cost metrics

| Metric | What | Why | Future alert (example) |
|--------|------|-----|------------------------|
| `request_cost_usd` | Total per `/council` or `/chat` | Tenant budgeting | &gt; ceiling per tenant/hour |
| `provider_cost_usd` | Per OpenAI / Anthropic call | Expensive-path detection | single call &gt; $0.05 |
| `expensive_path_flag` | Council used gpt-4o + synthesis + persist | Cost governance | rate &gt; threshold |

See `COST_GOVERNANCE.md` for policy; metrics implement policy later.

---

## Reliability metrics

| Metric | What | Why | Future alert (example) |
|--------|------|-----|------------------------|
| `degraded_response_count` | Experts returning degraded text | Provider/config issues | &gt; 10% of council |
| `subsystem_failure_count` | By subsystem + `category` | Targeted fixes | synthesis fail &gt; 5% |
| `circuit_breaker_open` | (Future) per provider | Stop cascading failures | open &gt; 1 min |

**Categories (align with code):** `timeout`, `auth_error`, `config_error`, `provider_unavailable`, `unknown_error`.

---

## What to implement first (recommended order)

1. **Structured log fields** — duration_ms, subsystem, category (extend `services/ops/structured_log.py`).
2. **Response metadata** (optional, non-breaking) — `timing_ms` in `/health`, `/ready`, `/council` for debugging.
3. **Aggregate counters** — in-process or Railway log parsing before Prometheus.
4. **Alerts** — only after 1–2 weeks of baseline data.

---

## What not to measure yet

- Full prompt/content bodies (privacy, cost).
- Raw API keys or `DATABASE_URL`.
- Per-user PII in metric labels.

---

## Dependencies

| Dependency | Risk ID |
|------------|---------|
| JSON log formatter | R-008 |
| Runtime load isolation | R-010 |
| Queue for async persist | R-011 |
| Latency instrumentation in code | R-012 |

---

READY FOR CHATGPT REVIEW
