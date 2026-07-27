# Inference Accounting (Pass 1)

Implementation-focused contract for BEN’s append-only inference ledger.

## Ownership chain

```text
ExecutionPlan          # policy / allow-deny boarding pass
    ↓
ExecutionContext       # minimal request-scoped IDs for metering
    ↓
model_gateway          # sole writer of accounting events
    ↓
provider attempt       # one HTTP/stream attempt via adapter
    ↓
InferenceCallRecord    # immutable ledger row (ben.inference_call_records)
```

| Component | Responsibility |
|-----------|----------------|
| `ExecutionPlan` | Owns policy (capability allow/deny). Does not meter. |
| `ExecutionContext` | Carries `request_id`, `execution_id`, org/workspace, pipeline. |
| `model_gateway` | Meters every provider attempt; creates ledger rows. |
| Provider adapters | Normalize usage only (`InferenceUsage`). No ledger writes. |

## Invariants

1. **One provider attempt = one append-only ledger record.**  
   Success, error, timeout, rejected, client disconnect, stream interruption, and missing usage each produce a row.

2. **A provider call is not a user request.**  
   One request / execution may produce many `InferenceCallRecord` rows (retries, fallbacks, tool loops).

3. **Multiple call records may share one `execution_id`.**  
   Correlate by `execution_id` and/or `request_id`. Distinct attempts have distinct `call_id` values.

4. **Unknown pricing is not a false zero.**  
   Ledger stores `cost_usd = NULL` with `cost_status` in `{unknown, unpriced}`; UI may show `0.0` for display only.

5. **Missing usage is explicit.**  
   `usage_status = missing` when the provider returns no usable token counts.

6. **Gateway meters; adapters normalize; ExecutionPlan owns policy.**  
   No second planner or governor in Pass 1.

7. **Prompts and completions are not stored for accounting.**  
   Ledger fields are identifiers, model/provider, usage, cost snapshot, latency, outcome — never prompt/response text.

## Persistence

- Table: `ben.inference_call_records`
- Migration: `009_inference_call_records` (`down_revision = 008_news_claims_e1`)
- Append-only inserts; no in-place updates in Pass 1
- Persist failures are soft: the user request continues; an operational error log is emitted with accounting fields (no silent drop)

### Production migration (do not run until merge)

```bash
alembic -c database/migrations/alembic.ini upgrade head
```

Or specifically:

```bash
alembic -c database/migrations/alembic.ini upgrade 009_inference_call_records
```

Rollback:

```bash
alembic -c database/migrations/alembic.ini downgrade 008_news_claims_e1
```

## Reconstruction

Given `request_id` or `execution_id`, query `ben.inference_call_records` to answer:

- how many provider calls happened
- which providers / models / api models
- input / output / reasoning / cached tokens
- latency, pricing version, usage/cost status, final cost
- outcome per attempt (including stream interruptions)

No application logs required.

## Out of scope (Pass 1)

Budget enforcement, InferenceBudget, governor mutations, aggregation, dashboards, correction ledgers.
