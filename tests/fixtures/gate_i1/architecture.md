# I1 architecture (isolated prototype)

```
measured BEN failure
        │
        ▼
  Failure Record (schema)
        │
        ▼
  classify from evidence
  (token Jaccard + span presence;
   ignores task_id / question_id)
        │
        ▼
  Fix Contract (allowed/forbidden modules,
  security invariants, latency budget, tests)
        │
        ▼
  existing-capabilities audit
  USE / ADAPT / LEARN / REJECT
        │
        ▼
  candidate 1..3 (fail-closed query prep only)
        │
        ├─ targeted span check (match by user_query)
        ├─ full isolated FTS gold benchmark
        ├─ isolation tests (cross-org / cross-workspace)
        └─ compare vs committed 48/50, 42/43, 7/7, MRL 1
        │
        ▼
  PASS / REJECT / NEEDS_REVIEW
        │
        └── recommendation only
            no merge, no deploy, no production flag change
```

Adaptive stop: first PASS, three REJECTs, or architecture beyond query-prep.

Allowed candidate category for lexical class: bounded `stem:*` / exact-stem atoms on existing `simple` FTS. Forbidden: embeddings, hybrid, rerank, vector DB, schema, auth/RLS.
