# Gate I1 — automated improvement loop prototype

**GATE I1: PASS**

Candidate `C1_PREFIX` is eligible for a human-reviewed implementation gate.

Production unchanged. Not merged. Not deployed. Railway FTS flags not touched.
`BEN_FTS_LEXICAL_EXPAND` default remains **off**.

Stop reason: `C1_PREFIX` passed all regression gates (C2/C3 not executed — adaptive stop).

---

## 1. I1 architecture

Measured failure → Failure Record → classify from evidence (not question ID) → Fix Contract → existing-capabilities audit (USE/ADAPT/LEARN/REJECT) → ≤3 fail-closed query-prep candidates → targeted span check + full isolated FTS + isolation tests → compare committed baseline → PASS / REJECT / NEEDS_REVIEW → **recommendation only**.

See `tests/fixtures/gate_i1/architecture.md`.

## 2. Failure record

Seeded from committed Gate P `BEN_EXISTING_FTS` residual. Classifier ignores `task_id`.

| field | value |
| --- | --- |
| failure_id | `i1-m09-fts` |
| benchmark_id | `gate_p_ben_existing_fts` |
| task_id (seed only) | M09 |
| prior Gate P label | RANKING_FAILURE |
| **classified** | **LEXICAL_VARIATION** |
| user_query | Where must the goods be delivered? |
| expected_evidence | named delivery place is 12 HaMelacha Street |
| retrieved_evidence (baseline) | pages=[7, 11] |
| expected_answer | 12 HaMelacha Street, Netanya |
| source_files | ben_gold_supply_agreement.pdf |
| retrieval_mode | chunks |
| severity | medium |
| security_relevant | false |
| repeat_count | 1 |

## 3. Fix Contract

| field | value |
| --- | --- |
| problem_class | LEXICAL_VARIATION |
| hypothesis | Bounded query expansion over sanitized Latin tokens may recover inflectional variants on existing simple FTS |
| expected_improvement | Lexical-variation misses recover the decisive span; correctness / recall / unanswerable do not drop |
| allowed_modules | retrieval query preparation |
| forbidden_modules | auth, tenant isolation, RLS, file ownership, provider routing, database schema |
| security_invariants | same org/workspace/file filtering; no broader unauthorized source set; user tokens never inject tsquery operators; `:*` atoms expander-only |
| performance_budget | FTS timeout 200ms; mean FTS latency ≤ 2× same-harness control |
| required_tests | Gate M/P isolated FTS, isolation tests, unanswerable precision |
| rollback_strategy | leave `BEN_FTS_LEXICAL_EXPAND` unset/off; do not merge; do not deploy |
| scope | query_prep |
| risk_level | low |

## 4. Classification logic

Token Jaccard between `user_query` and `expected_evidence` (significant tokens, min length 3). Does not switch on question ID.

- Low Jaccard + span missing + chunks returned → `LEXICAL_VARIATION`
- High Jaccard + span missing → `RANKING_FAILURE`
- Span injected but answer missing → `MODEL_REASONING_FAILURE` / `TABLE_LAYOUT`
- Empty retrieval → lexical vs `CONTEXT_LOSS` by overlap

This seed: query tokens `{where, must, goods, delivered}` vs evidence `{named, delivery, place, hamelacha, street}` → Jaccard 0 → **LEXICAL_VARIATION** (overrides prior RANKING_FAILURE).

## 5. Existing-capabilities audit

Live Postgres probe on generic doc `the delivery window closed after inspection`:

| probe | query | matched |
| --- | --- | --- |
| simple exact inflection | delivered | false |
| simple prefix inflection | deliver:* | **true** |
| simple surface form | delivery | true |
| english query on simple vec | delivered | false |
| english/english inflection | delivered | false |

| verdict | mechanism |
| --- | --- |
| **USE** | postgres `simple` FTS prefix operator `stem:*` on existing `text_tsv` GIN |
| **ADAPT** | `build_or_tsquery` appends expander atoms when flag=prefix |
| **LEARN** | future morphology table if prefix under/over-stems a later class |
| **REJECT** | english config (schema), embeddings/hybrid/rerank/vector DB, new Python deps, provider-native PDF |

Chosen: fail-closed query-prep prefix atoms. No new dependency.

## 6. Candidate fixes tried

Maximum 3. Adaptive stop after first PASS.

| id | approach | executed | decision |
| --- | --- | --- | --- |
| C1_PREFIX | extra `stem:*` atoms in the same OR tsquery | yes | **PASS** |
| C2_VARIANTS | exact stems without prefix | not run | — |
| C3_SECOND_QUERY | second FTS round-trip / hit union | not run | would be NEEDS_REVIEW if C1/C2 failed |

C1 extra atoms for the seed query (generic `-ed` strip, not a document synonym): `['deliver:*']`.

## 7. Benchmark comparison

Committed Gate P `BEN_EXISTING_FTS` vs same-harness control (expand off) vs C1.

| metric | committed | control (off) | C1 prefix |
| --- | --- | --- | --- |
| correctness | 48/50 | 48/50 | **49/50** |
| decisive recall | 42/43 | 42/43 | **43/43** |
| unanswerable | 7/7 | 7/7 | **7/7** |
| MRL | 1 | 1 | **0** |
| residual frontier | RANKING_FAILURE 1, MODEL_REASONING 1 | same | MODEL_REASONING 1 only |

Targeted: seed query recovered page **2** (was 7, 11). Decisive span injected. Extractive answer OK.

Remaining miss is model-arithmetic (not retrieval).

## 8. Security regression

With `BEN_FTS_LEXICAL_EXPAND=prefix`:

- `test_same_filename_across_workspaces_no_leak` PASS
- `test_cross_org_isolation` PASS
- additional Gate 4A DB tests (early/middle/late, Hebrew, zero-hit no random chunks, allowlist other workspace stays Gate 3D, operator injection) PASS
- no unrelated source expansion (gold file only)
- user `:*` tokens still rejected (`delivered:*` / `foo:*` dropped)
- default off: Gate M workspace `chunk_retrieval_enabled` is False; tsquery remains `where | must | goods | delivered`

Auth / RLS / tenant isolation code was not modified.

## 9. Latency comparison

| | control off | C1 prefix | budget |
| --- | --- | --- | --- |
| mean FTS ms | 0.842 | 0.866 | < 200 timeout; ≤ 2× control |
| max FTS ms | 3.06 | 1.33 | < 200 |
| mean wall ms | 3.986 | 3.896 | — |

Delta is noise. Within budget.

## 10. Decision

**GATE I1: PASS**

Candidate fix is eligible for a human-reviewed implementation gate.

## 11. Files changed (isolated branch)

- `services/workspace_files/query_expansion.py` (new, fail-closed)
- `services/workspace_files/chunk_retriever.py` (hook after sanitizing user tokens)
- `services/improvement/` (schema, classify, contract, capabilities, evaluate, loop)
- `scripts/run_gate_i1.py`
- `tests/test_query_expansion.py`
- `tests/test_gate_i1_improvement_loop.py`
- `tests/fixtures/gate_i1/`

No auth, RLS, tenant, schema, provider-routing, or Railway variable changes.

## 12. Generic vs benchmark-specific

**Generic.** Expander has no question ID, no gold locator, no street name. Latin tokens length ≥ 6 ending in inflectional suffixes yield `stem:*`. The seed query produces `deliver:*`, which matches `delivery` on `simple` FTS.

Hardcoding scan of expander + retriever: zero hits for benchmark literals.

## 13. Recommendation for next gate

Keep this branch unmerged as production.

1. Human-reviewed implementation gate: enable `BEN_FTS_LEXICAL_EXPAND=prefix` **only** on an isolated/canary workspace allowlist (same fail-closed FTS flag pattern). Re-run Gate M/P + isolation.
2. Do not enable globally from I1.
3. Do not change ranking, chunking, `PER_FILE_MAX_CHARS`, embeddings, or schema.
4. Rollback is unset the expand env var (default off) and redeploy the same SHA if it were ever set.
5. Residual M33 remains a model-reasoning issue, not this loop.

**Do not merge. Do not deploy. Do not change production flags.**
