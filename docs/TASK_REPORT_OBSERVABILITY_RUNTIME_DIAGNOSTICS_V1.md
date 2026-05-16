# TASK REPORT

## 1. Task Name

BEN Observability & Runtime Diagnostics v1

## 2. Branch

`feature/observability-runtime-diagnostics-v1`

## 3. Goal

Tier-1 operational visibility: structured lifecycle events, provider timing, safe runtime snapshot, no prompts/secrets/PII in diagnostics.

## 4. Files Changed

| File | Change type |
|------|-------------|
| `services/ops/runtime_events.py` | added |
| `services/ops/runtime_diagnostics.py` | added |
| `services/ops/json_log_formatter.py` | modified |
| `services/ops/load_governance.py` | modified |
| `services/council_service.py` | modified |
| `main.py` | modified |
| `tests/test_runtime_diagnostics.py` | added |
| `docs/BEN_RUNTIME_CONTRACTS.md` | modified |
| `docs/RISK_REGISTER.md` | modified |

## 5. Code Changes

### Diagnostics model

- **Request scope:** `request_id`, `route`, `tenant_type`, `tenant_hash` (SHA-256 prefix), `dominant_language` (script hint).
- **Process scope:** `RuntimeMetricsStore` — provider outcome counters, council/synthesis aggregates, overload/persistence failure counts.
- **Forbidden fields** stripped before emit (`message`, `question`, `content`, tokens, raw tenant IDs).

### Structured event schema

| Event | When |
|-------|------|
| `request_started` / `request_completed` / `request_failed` | `/chat`, `/council` HTTP lifecycle |
| `council_started` / `council_completed` | Council execution |
| `provider_timeout` | Provider call with `outcome=timeout` |
| `overload_rejected` | Load governor reject |
| `persistence_failed` | Thread/KO persist errors |
| `runtime_snapshot` | `GET /runtime/snapshot` |

### Snapshot fields (`GET /runtime/snapshot`)

`active_chat_requests`, `active_council_requests`, `inflight_total`, `rejected_overload_requests`, `overload_rejected_counts`, `provider_timeout_counts`, `provider_*_counts`, `provider_duration_ms_total`, `degraded_council_count`, `council_completed_count`, `council_duration_ms_total`, `persistence_failed_count`, synthesis counters.

### Provider timing behavior

Each expert call and synthesis records `duration_ms` + normalized `outcome` (`ok` | `timeout` | `degraded` | `error`). Anthropic/OpenAI/Google mapped consistently; synthesis attributed to OpenAI.

## 6. Verification Executed

```bash
cd c:\BEN-V2
python -m pytest tests/test_runtime_diagnostics.py tests/test_load_governance.py -q
```

Browser/runtime matrix (short/long council, overload, degraded, refresh, duplicate submit): **NOT EXECUTED**.

## 7. Verification Results

| Check | Result | Notes |
|-------|--------|-------|
| Lifecycle + snapshot unit tests | **PASS** | 9 diagnostics tests |
| Load governance regression | **PASS** | 7 tests |
| No prompt/PII in caplog | **PASS** | `test_emit_runtime_event_filters_forbidden` |
| Provider timing counters | **PASS** | |
| Overload snapshot coherence | **PASS** | |
| Browser stress matrix | **NOT VERIFIED** | Required for FIXED |

## 8. Remaining Operational Blind Spots

- Per-process snapshot only (multi-worker aggregation missing — R-039).
- JSON formatter does not yet index all custom fields in dashboards (logs only).
- No distributed trace correlation beyond `request_id`.
- Prod Railway log sampling not validated.

## 9. Future Observability Roadmap

- OpenTelemetry export (optional vendor-neutral).
- Per-tenant diagnostic quotas (hashed tenant dimension).
- Centralized metrics (Prometheus/Grafana) when ops ready.
- SLO dashboards on `council_duration_ms` p95 and `provider_timeout_counts`.

## 10. Risks Updated

- **R-019** PARTIAL — runtime observability events + snapshot
- **R-036** PARTIAL — overload visible in snapshot
- **R-038** PARTIAL — provider timing counters
- **R-039** OPEN — multi-instance snapshot

---

READY FOR CHATGPT REVIEW
