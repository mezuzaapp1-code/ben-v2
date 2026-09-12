# Gate D1 production canary result

Measured 2026-09-12 against production SHA `44ef277d65eb0675ad9f23d6922209d66d2f5728`.

Isolated allowlist only. Global FTS was not enabled. Temporary canary vars were
deleted and production was redeployed on the same SHA so the running process
dropped the allowlist.

| Metric | Gate P isolated FTS | Gate D1 production canary |
| --- | --- | --- |
| Decisive-span recall | 42/43 | 42/43 |
| MRL | 1/50 | 1/50 |
| Late | 17/17 | 17/17 |
| Exceptions | 5/5 | 5/5 |
| Unanswerable | 7/7 | 7/7 |
| Citation/page | 42/43 | 42/43 |
| Residuals | M09 RANKING_FAILURE, M33 MODEL_REASONING_FAILURE | same |

Observable FTS proof: `retrieval_mode=chunks`, chunk UUIDs, `evidence_pages`,
and `response_evidence.retrieval_mode=chunks` on 49/50 gold questions. M48 and
the dedicated nonsense query used `retrieval_mode=empty` / `no_lexical_match`
with HTTP 200.

Non-canary control workspace stayed on `retrieval_mode=off` / `flag_off`.
