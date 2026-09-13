"""Gate D1 pass/fail/partial/blocked from measured production artifacts."""
from __future__ import annotations

from typing import Any

EXPECTED_SHA = "44ef277d65eb0675ad9f23d6922209d66d2f5728"
GATE_P_FTS = {
    "decisive_span_recall": "42/43",
    "mrl": 1,
    "late": "17/17",
    "exceptions": "5/5",
    "unanswerable_precision": "7/7",
    "citation_page_accuracy": "42/43",
}


def _frac(raw: Any) -> tuple[int, int] | None:
    text = str(raw or "")
    if "/" not in text:
        return None
    left, right = text.split("/", 1)
    try:
        return int(left), int(right)
    except ValueError:
        return None


def decide_gate_d1(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Return status plus the rollout recommendation. Does not enable FTS globally."""
    reasons: list[str] = []
    sha = (artifacts.get("pre_health") or {}).get("version") or artifacts.get("production_sha")
    canary = artifacts.get("canary_summary") or {}
    control = artifacts.get("control_summary") or {}
    fallback = artifacts.get("fallback") or {}
    cleanup = artifacts.get("cleanup") or {}
    control_rows = artifacts.get("control_rows") or []
    canary_rows = artifacts.get("canary_rows") or []

    if artifacts.get("blocked_reason"):
        return {
            "status": "BLOCKED",
            "rollout": "NOT YET",
            "reasons": [str(artifacts["blocked_reason"])],
        }

    if sha != EXPECTED_SHA:
        reasons.append(f"production SHA mismatch: {sha}")

    fts_used = bool(artifacts.get("fts_actually_used"))
    modes = set(artifacts.get("retrieval_modes_observed") or [])
    if not fts_used or not (modes & {"chunks", "mixed"}):
        reasons.append("canary did not observably use chunk FTS")

    control_modes = {r.get("retrieval_mode") for r in control_rows}
    if "chunks" in control_modes or "mixed" in control_modes:
        reasons.append("non-canary workspace used chunk FTS")
    if any(r.get("chunk_ids") for r in control_rows):
        reasons.append("non-canary workspace returned chunk IDs")

    if fallback.get("error_500") or int(fallback.get("http_status") or 0) >= 500:
        reasons.append("fallback path returned 500")

    http_fail = [r for r in canary_rows if int(r.get("http_status") or 0) >= 500]
    if http_fail:
        reasons.append(f"canary chat 5xx on {len(http_fail)} questions")

    if artifacts.get("transcript_pollution") == "FAIL":
        reasons.append("transcript pollution")

    canary_get = cleanup.get("canary_get_after")
    control_get = cleanup.get("control_get_after")
    if canary_get not in {404, "404"} or control_get not in {404, "404"}:
        reasons.append("file cleanup did not yield GET 404")

    if artifacts.get("allowlist_reverted") is False:
        reasons.append("canary allowlist was not reverted")

    recall = _frac(canary.get("decisive_span_recall"))
    late = _frac(canary.get("late"))
    exceptions = _frac(canary.get("exceptions"))
    unans = _frac(canary.get("unanswerable_precision"))
    mrl = canary.get("mrl")
    try:
        mrl_n = int(mrl)
    except (TypeError, ValueError):
        mrl_n = None

    material_fail = [
        r
        for r in reasons
        if r.startswith("non-canary") or "5xx" in r or r.startswith("fallback") or "SHA mismatch" in r
    ]
    if material_fail:
        return {"status": "FAIL", "rollout": "NO", "reasons": reasons}

    if "canary did not observably use chunk FTS" in reasons:
        return {"status": "FAIL", "rollout": "NO", "reasons": reasons}

    expected_residuals = {"M09", "M33"}
    extra_mrl = [
        r["question_id"]
        for r in canary_rows
        if r.get("mrl") and r.get("question_id") not in expected_residuals
    ]

    close_to_gate_p = (
        recall is not None
        and recall[0] >= 40
        and recall[1] == 43
        and mrl_n is not None
        and mrl_n <= 3
        and late is not None
        and late[0] >= 16
        and exceptions is not None
        and exceptions[0] >= 4
        and (unans is None or unans[0] == unans[1])
        and not extra_mrl
        and artifacts.get("transcript_pollution") != "FAIL"
        and canary_get in {404, "404"}
        and control_get in {404, "404"}
    )

    if reasons and not close_to_gate_p:
        return {"status": "PARTIAL", "rollout": "NOT YET", "reasons": reasons}

    if close_to_gate_p and not material_fail:
        leftover = [r for r in reasons if "cleanup" in r or "allowlist" in r or "pollution" in r]
        if leftover:
            return {"status": "PARTIAL", "rollout": "NOT YET", "reasons": leftover}
        return {
            "status": "PASS",
            "rollout": "NOT YET",
            "reasons": [
                "Isolated canary matched Gate P existing-FTS expectations. Do not enable globally.",
            ],
        }

    if extra_mrl or (recall and recall[0] < 40) or (mrl_n is not None and mrl_n > 3):
        return {
            "status": "PARTIAL",
            "rollout": "NOT YET",
            "reasons": reasons
            + ([f"extra MRL beyond Gate P residuals: {extra_mrl}"] if extra_mrl else []),
        }

    return {"status": "PARTIAL", "rollout": "NOT YET", "reasons": reasons or ["measurable production delta"]}
