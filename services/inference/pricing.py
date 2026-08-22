"""Versioned pricing lookup for inference accounting."""
from __future__ import annotations

from services.inference.contracts import InferenceCost, InferencePricingSnapshot, InferenceUsage
from services.providers.model_registry import cached_input_rate, token_rates


def pricing_version() -> str:
    from services.providers.model_registry import _load_registry

    data = _load_registry()
    version = data.get("version")
    return str(version or "unknown")


def resolve_pricing_snapshot(
    *,
    provider: str,
    model: str,
    usage: InferenceUsage | None = None,
) -> InferencePricingSnapshot:
    prov = (provider or "").strip().lower()
    mid = (model or "").strip()
    prompt_tokens = int(usage.input_tokens) if usage is not None else 0
    ir, or_ = token_rates(prov, mid, prompt_tokens=prompt_tokens)
    cached = cached_input_rate(prov, mid, prompt_tokens=prompt_tokens)
    # Cached/reasoning inherit input/output when the registry has no model-specific cached rate.
    return InferencePricingSnapshot(
        pricing_version=pricing_version(),
        provider=prov,
        model=mid,
        input_usd_per_token=float(ir),
        output_usd_per_token=float(or_),
        cached_input_usd_per_token=float(cached) if cached is not None else float(ir),
        reasoning_usd_per_token=float(or_),
        currency="USD",
    )


def calculate_cost(usage: InferenceUsage, snapshot: InferencePricingSnapshot) -> InferenceCost:
    if usage.usage_status == "missing":
        return InferenceCost(
            amount_usd=None,
            currency=snapshot.currency,
            cost_status="unknown",
            pricing_version=snapshot.pricing_version,
        )
    if snapshot.input_usd_per_token is None or snapshot.output_usd_per_token is None:
        return InferenceCost(
            amount_usd=None,
            currency=snapshot.currency,
            cost_status="unpriced",
            pricing_version=snapshot.pricing_version,
        )

    billable_input = max(0, int(usage.input_tokens) - int(usage.cached_input_tokens))
    cached = max(0, int(usage.cached_input_tokens))
    output = max(0, int(usage.output_tokens))
    reasoning = max(0, int(usage.reasoning_tokens))

    cached_rate = snapshot.cached_input_usd_per_token
    if cached_rate is None:
        cached_rate = snapshot.input_usd_per_token
    reasoning_rate = snapshot.reasoning_usd_per_token
    if reasoning_rate is None:
        reasoning_rate = snapshot.output_usd_per_token

    amount = (
        billable_input * float(snapshot.input_usd_per_token)
        + cached * float(cached_rate)
        + output * float(snapshot.output_usd_per_token)
        + reasoning * float(reasoning_rate)
    )
    if amount == 0.0 and usage.normalized_total() == 0:
        status: str = "zero"
    else:
        status = "priced"
    return InferenceCost(
        amount_usd=round(float(amount), 8),
        currency=snapshot.currency,
        cost_status=status,  # type: ignore[arg-type]
        pricing_version=snapshot.pricing_version,
    )
