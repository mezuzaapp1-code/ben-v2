# BEN Cost Governance

Cost-aware operational policy for BEN-V2. **Never scale into losses.** This phase is documentation only; enforcement is future work.

---

## Principles

1. **Every request should have a knowable upper cost bound** before execution where possible.
2. **Expensive paths require justification** — model tier, token limits, and tenant tier.
3. **Optional layers are cost-cut first** — synthesis and persistence before dropping core experts.
4. **Tenant budgeting is future** — design metrics and ceilings now (`INSTRUMENTATION_PLAN.md`).

---

## Cost-aware routing (future)

| Tier | Default models | Council path |
|------|----------------|--------------|
| Free / FAST | `gpt-4o-mini`, minimal tokens | Avoid gpt-4o unless escalated |
| Pro | `gpt-4o` + Anthropic legal | Full council allowed within budget |
| Deliberate / enterprise | Configurable per tenant | Full council + synthesis within ceiling |

**Today:** Council uses fixed models (gpt-4o, gpt-4o-mini, Anthropic legal, synthesis gpt-4o-mini). Routing changes require Dynamic Provider Config (R-007, T-106).

---

## Expensive model escalation policy

Escalation **up** (mini → 4o → opus-class) only when:

- Tenant tier allows it.
- Estimated incremental cost &lt; per-request ceiling.
- Prior fast path failed with `config_error` or quality gate (future).

Escalation **down** (degrade) when:

- Approaching tenant or global ceiling.
- Timeout or `provider_unavailable` on expensive model.
- Synthesis-only: use cheaper model first (current default `gpt-4o-mini`).

---

## Synthesis cost policy

| Rule | Rationale |
|------|-----------|
| Synthesis uses **cheaper OpenAI model by default** | Already `SYNTHESIS_MODEL_DEFAULT` / env override |
| Synthesis timeout **10s** | Limits burn on hung calls |
| Failed synthesis **$0 incremental** beyond experts | No second billing path |
| Persist only on successful synthesis | Avoid DB cost without user value |

---

## Future tenant budgeting

| Mechanism | Description |
|-----------|-------------|
| Per-tenant daily USD ceiling | Hard stop or degrade to mini models |
| Per-tenant council quota | N council calls / day |
| Per-request `cost_usd` cap | Reject or truncate before providers |
| Usage reporting | Aggregate `cost_usd` from responses + logs |

---

## Future usage ceilings (global)

- Max concurrent DELIBERATE requests per instance.
- Max total provider spend / hour (circuit breaker to degraded mode).
- Alert when daily spend &gt; forecast.

---

## Never scale into losses

Before increasing traffic or model quality:

1. Estimate cost per council request (experts + synthesis).
2. Compare to revenue or budget per tenant.
3. Instrument `request_cost_usd` and `expensive_path_flag`.
4. Do not enable autoscaling without cost alerts (R-009, R-012).

---

## Related docs

- `TIMING_GOVERNANCE.md` — time budgets limit cost exposure.
- `INSTRUMENTATION_PLAN.md` — cost metrics definitions.
- `docs/RISK_REGISTER.md` — R-009, R-010–R-012.

---

READY FOR CHATGPT REVIEW
