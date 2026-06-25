"""Cost Engineering matrix parsing and historical anomaly detection for supplier tenders."""
from __future__ import annotations

import re
import statistics
import uuid
from typing import Any

_AMOUNT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)(?!\d)")

_LAYER_PATTERNS: dict[str, re.Pattern[str]] = {
    "base_material_cost": re.compile(
        r"(?:materials?|rebar|concrete|aggregates?)\s*[:\-]?\s*(\d[\d,\.]+)",
        re.I,
    ),
    "logistics_freight_overhead": re.compile(
        r"(?:freight|logistics|shipping|delivery|transport)\s*[:\-]?\s*(\d[\d,\.]+)",
        re.I,
    ),
    "operational_overheads": re.compile(
        r"(?:operational|overhead|handling|unloading|site\s+prep)\s*[:\-]?\s*(\d[\d,\.]+)",
        re.I,
    ),
    "supplier_margin_risk_premium": re.compile(
        r"(?:margin|risk|premium|markup|profit)\s*[:\-]?\s*(\d[\d,\.]+)",
        re.I,
    ),
}

_DEFAULT_LAYER_SHARES = {
    "base_material_cost": 0.68,
    "logistics_freight_overhead": 0.12,
    "operational_overheads": 0.10,
    "supplier_margin_risk_premium": 0.10,
}

_HISTORICAL_LAYER_MEDIANS = {
    "base_material_cost": 0.70,
    "logistics_freight_overhead": 0.10,
    "operational_overheads": 0.12,
    "supplier_margin_risk_premium": 0.08,
}

_ANOMALY_THRESHOLD_PCT = 15.0


def _to_float(raw: str) -> float:
    cleaned = re.sub(r"[^\d.]", "", str(raw).replace(",", ""))
    return float(cleaned)


def _extract_layer_amounts(text: str, total: float) -> dict[str, float]:
    found: dict[str, float] = {}
    for layer, pattern in _LAYER_PATTERNS.items():
        m = pattern.search(text)
        if m:
            try:
                found[layer] = _to_float(m.group(1))
            except ValueError:
                continue

    if found:
        missing = [layer for layer in _DEFAULT_LAYER_SHARES if layer not in found]
        remaining = round(total - sum(found.values()), 2)
        if len(missing) == 1 and remaining > 0:
            found[missing[0]] = remaining
        elif missing and remaining > 0:
            weight = sum(_DEFAULT_LAYER_SHARES[m] for m in missing)
            for layer in missing:
                share = _DEFAULT_LAYER_SHARES[layer] / weight
                found[layer] = round(remaining * share, 2)
    else:
        for layer, share in _DEFAULT_LAYER_SHARES.items():
            found[layer] = round(total * share, 2)

    # Normalize total drift proportionally across captured layers
    drift = round(total - sum(found.values()), 2)
    if abs(drift) >= 0.01 and found:
        layer_sum = sum(found.values())
        if layer_sum > 0:
            for layer in found:
                found[layer] = round(found[layer] + drift * (found[layer] / layer_sum), 2)

    return found


def parse_supplier_bid(
    bid_text: str,
    *,
    supplier_name: str | None = None,
    total_bid_nis: float | None = None,
) -> dict[str, Any]:
    text = (bid_text or "").strip()
    if not text and total_bid_nis is None:
        raise ValueError("bid_text or total_bid_nis is required")

    total = total_bid_nis
    if total is None:
        amounts = [_to_float(m.group(1)) for m in _AMOUNT_RE.finditer(text.replace(",", ""))]
        if not amounts:
            raise ValueError("Could not extract bid total from supplier text")
        total = max(amounts)

    layers = _extract_layer_amounts(text, float(total))
    matrix = {
        "base_material_cost": layers["base_material_cost"],
        "logistics_freight_overhead": layers["logistics_freight_overhead"],
        "operational_overheads": layers["operational_overheads"],
        "supplier_margin_risk_premium": layers["supplier_margin_risk_premium"],
    }
    matrix["total_bid_nis"] = round(sum(matrix.values()), 2)
    matrix["layer_pcts"] = {
        k: round((v / matrix["total_bid_nis"]) * 100, 2) if matrix["total_bid_nis"] else 0
        for k, v in matrix.items()
        if k != "total_bid_nis" and k != "layer_pcts"
    }

    supplier = (supplier_name or "").strip()
    if not supplier:
        sm = re.search(r"(?:from|supplier|vendor)\s+([A-Za-z\u0590-\u05FF][\w\s\-]{2,40})", text, re.I)
        supplier = sm.group(1).strip() if sm else "Unknown Supplier"

    return {
        "supplier_name": supplier[:256],
        "bid_text": text[:4000] if text else None,
        "cost_matrix": matrix,
    }


def build_historical_baseline(
    ledger_expenses: list[dict[str, Any]],
    prior_tenders: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive historical layer share medians from ledger + prior accepted tenders."""
    layer_samples: dict[str, list[float]] = {k: [] for k in _DEFAULT_LAYER_SHARES}

    for tender in prior_tenders:
        cm = tender.get("cost_matrix") or {}
        total = float(cm.get("total_bid_nis") or 0)
        if total <= 0:
            continue
        for layer in layer_samples:
            val = cm.get(layer)
            if val is not None:
                layer_samples[layer].append(float(val) / total)

    expense_amounts = [
        float(e.get("amount") or 0) for e in ledger_expenses if float(e.get("amount") or 0) > 0
    ]
    median_expense = statistics.median(expense_amounts) if expense_amounts else None

    baseline_shares = {}
    for layer, samples in layer_samples.items():
        if len(samples) >= 2:
            baseline_shares[layer] = round(statistics.median(samples), 4)
        else:
            baseline_shares[layer] = _HISTORICAL_LAYER_MEDIANS[layer]

    return {
        "layer_share_medians": baseline_shares,
        "median_expense_nis": median_expense,
        "sample_tender_count": len(prior_tenders),
        "sample_expense_count": len(expense_amounts),
    }


def detect_cost_anomalies(
    cost_matrix: dict[str, Any],
    baseline: dict[str, Any],
) -> list[dict[str, Any]]:
    total = float(cost_matrix.get("total_bid_nis") or 0)
    if total <= 0:
        return []

    anomalies: list[dict[str, Any]] = []
    medians = baseline.get("layer_share_medians") or _HISTORICAL_LAYER_MEDIANS
    layer_pcts = cost_matrix.get("layer_pcts") or {}

    for layer, median_share in medians.items():
        actual_share = float(layer_pcts.get(layer) or 0) / 100.0
        if actual_share <= 0:
            continue
        delta_pct = round(((actual_share - median_share) / median_share) * 100, 2) if median_share else 0
        if delta_pct >= _ANOMALY_THRESHOLD_PCT:
            severity = "high" if delta_pct >= 25 else "medium"
            label = layer.replace("_", " ")
            anomalies.append(
                {
                    "layer": layer,
                    "severity": severity,
                    "delta_pct": delta_pct,
                    "actual_share_pct": round(actual_share * 100, 2),
                    "baseline_share_pct": round(median_share * 100, 2),
                    "message": f"Inflated {label}: {delta_pct}% above historical median",
                }
            )

    median_expense = baseline.get("median_expense_nis")
    if median_expense and total > float(median_expense) * 1.35:
        anomalies.append(
            {
                "layer": "total_bid_nis",
                "severity": "high",
                "delta_pct": round(((total - median_expense) / median_expense) * 100, 2),
                "message": f"Total bid exceeds historical median expense by {round(((total/median_expense)-1)*100, 1)}%",
            }
        )

    return anomalies


def new_tender_id() -> str:
    return str(uuid.uuid4())
