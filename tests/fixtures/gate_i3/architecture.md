# Gate I3 — BEN Improvement Loop architecture

Internal framework only. Not autonomous production self-modification.

## Flow

observe measured failure
→ classify
→ Failure Record
→ bounded Fix Contract
→ evaluate candidate measurements
→ required regression gates
→ recommendation (PASS | REJECT | NEEDS_REVIEW | BLOCKED)

Human-reviewed gates remain required for merge, deploy, production flags,
and rollout.

## Authority boundary

`services/improvement` may produce:

- Failure Record
- Fix Contract
- isolated candidate descriptions
- test/evaluation results
- `improvement_run.json`
- recommendation

It must not merge, deploy, modify production flags, mutate production
schema/data, change secrets, weaken auth/RLS/tenant isolation, or approve
financial or permission changes.

`has_production_authority(*)` is always false.
`human_review_required` is always true.
HIGH and CRITICAL are never eligible for automatic production promotion.

## Failure taxonomy

LEXICAL_VARIATION, RANKING_FAILURE, CONTEXT_LOSS, EXCEPTION_MISSED,
MULTI_HOP, MODEL_REASONING_FAILURE, TABLE_LAYOUT, WRONG_SOURCE,
CROSS_FILE, ROUTING_FAILURE, TOOL_FAILURE, STRUCTURED_OUTPUT_FAILURE,
BUSINESS_MEMORY_FAILURE, AGENT_ACTION_FAILURE, PERMISSION_FAILURE, OTHER.

Only LEXICAL_VARIATION is auto-evaluable in this framework. M33 remains
MODEL_REASONING_FAILURE and is out of scope. M09 is an I1/I2 fixture,
not product hardcoding.

## Risk policy

- LOW — bounded query prep, non-security prompt formatting, observability
- MEDIUM — retrieval ranking, context assembly, model routing
- HIGH — external tool actions, business-memory writes, customer communication
- CRITICAL — auth, RLS, tenant isolation, payments, secrets, permissions

## Clustering

Token Jaccard over sanitized query tokens. No vector database.
one-off / repeated / systemic. One-off production errors do not open
engineering work.

## Priority

User impact, frequency, severity, diagnosis confidence, expected
improvement, implementation risk, estimated cost. Not benchmark-score-only.

## Evaluation (fail closed)

Target improvement, baseline metrics, security, isolation, correctness,
latency, cost, unrelated behavior, hardcoding, scope. A local improvement
with a material regression elsewhere is REJECTED.

## Adaptive stop

Default max 3 candidate approaches. Stop when one satisfies the contract,
max candidates fail, scope is exceeded, the failure is security-relevant,
evidence is insufficient, or an architectural change is required.

## Reuse

- `significant_tokens_in_order` (classifier + clustering)
- `query_expansion.ALLOWED_MODULES` / `FORBIDDEN_MODULES`
- Gate M/P FTS baseline fractions (48/50, 42/43, 7/7) as evaluation fixtures
- I1/I2 measured lexical candidate as a replay fixture
- structured provenance answers (not a second orchestrator)

## Intentionally not built

Dashboard, autonomous git merge/deploy, production mutation APIs, generic
agent framework, message brokers, embeddings/vector DB, retrieval redesign,
Business Agent architecture change, production behavior change.
