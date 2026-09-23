"""Media tariff estimate, separate from transport and token inference accounting.

Standard paid tariff verified 2026-09-22 at
https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-image
No invoice charge is inferred. Unknown dimensions remain unknown.
"""
from decimal import Decimal
from services.media.contracts import GEMINI_IMAGE_MODEL, BFL_IMAGE_MODEL

PRICING_VERSION = "google-gemini-3.1-flash-image-standard-2026-09-22"


def account(usage: dict, *, width: int, height: int, model: str = GEMINI_IMAGE_MODEL) -> dict:
    dimensions = {**usage, "resolution_tier": "1K", "width": width, "height": height}
    reported = usage.get("provider_usage") or {}
    if model == BFL_IMAGE_MODEL:
        version = "bfl-reported-credits-usd-2026-09-23"
        credits = reported.get("cost")
        estimate = Decimal(str(credits)) * Decimal("0.01") if credits is not None else None
        dimensions["cost_estimate"] = {"schema_version": "media-cost-v1", "pricing_version": version,
            "components": {"provider_reported_credits": credits}, "status": "complete" if estimate is not None else "partial",
            "missing_reason": None if estimate is not None else "provider_cost_not_reported",
            "actual_charge_source": "not_reported", "currency": "USD"}
        # Provider credits are recorded with their submission/settled provenance.
        # This conversion is not an invoice charge; no token approximation.
        return {"usage_dimensions": dimensions, "estimated_cost": estimate, "pricing_version": version, "actual_charge": None}
    if model != GEMINI_IMAGE_MODEL:
        raise ValueError("unsupported media accounting model")
    # Native per-image tariff; never manufacture an image token count.
    components = {"image_output_usd": "0.06720000"}
    for key, rate, label in (("total_input_tokens", "0.50", "input_usd"),
                              ("total_thought_tokens", "3.00", "thinking_usd")):
        if key in reported:
            components[label] = str(Decimal(reported[key]) * Decimal(rate) / 1_000_000)
    modalities = reported.get("output_tokens_by_modality") or []
    # Complete output accounting requires a consistent provider modality split.
    split_complete = (bool(modalities) and
                      all(e["modality"] in ("image", "text") for e in modalities) and
                      sum(e["tokens"] for e in modalities) == reported.get("total_output_tokens"))
    if split_complete:
        components["text_output_usd"] = str(sum(Decimal(e["tokens"]) for e in modalities
                                                if e["modality"] == "text") * Decimal(3) / 1_000_000)
    complete = (split_complete and "input_usd" in components and "thinking_usd" in components
                and reported.get("total_cached_tokens", 0) == 0
                and reported.get("total_tool_use_tokens", 0) == 0)
    estimate = sum(Decimal(v) for v in components.values()) if complete else None
    dimensions["cost_estimate"] = {
        "schema_version": "media-cost-v1", "pricing_version": PRICING_VERSION,
        "components": components, "status": "complete" if complete else "partial",
        "missing_reason": None if complete else "usage_breakdown_incomplete_or_unsupported",
        "actual_charge_source": "not_reported", "currency": "USD",
    }
    return {"usage_dimensions": dimensions, "estimated_cost": estimate,
            "pricing_version": PRICING_VERSION, "actual_charge": None}
