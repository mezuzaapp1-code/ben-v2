"""In-process Improvement Loop metrics. No customer content."""

from __future__ import annotations

from collections import Counter
from time import perf_counter
from typing import Any

_COUNTS: Counter[str] = Counter()
_DURATION_MS: list[float] = []


def reset() -> None:
    _COUNTS.clear()
    _DURATION_MS.clear()


def incr(name: str, n: int = 1) -> None:
    _COUNTS[name] += n


def observe_duration_ms(ms: float) -> None:
    _DURATION_MS.append(float(ms))


def snapshot() -> dict[str, Any]:
    durations = list(_DURATION_MS)
    return {
        "failures_observed": _COUNTS.get("failures_observed", 0),
        "failures_classified": _COUNTS.get("failures_classified", 0),
        "clusters_created": _COUNTS.get("clusters_created", 0),
        "candidates_evaluated": _COUNTS.get("candidates_evaluated", 0),
        "decision_pass": _COUNTS.get("decision_pass", 0),
        "decision_reject": _COUNTS.get("decision_reject", 0),
        "decision_needs_review": _COUNTS.get("decision_needs_review", 0),
        "decision_blocked": _COUNTS.get("decision_blocked", 0),
        "regressions_caught_before_production": _COUNTS.get("regressions_caught", 0),
        "mean_evaluation_duration_ms": round(sum(durations) / len(durations), 3) if durations else None,
        "estimated_cost": _COUNTS.get("estimated_cost_units", 0),
        "production_incidents_attributable_to_promoted_fixes": 0,
    }


class timed:
    def __enter__(self):
        self._t0 = perf_counter()
        return self

    def __exit__(self, *exc):
        observe_duration_ms((perf_counter() - self._t0) * 1000.0)
        return False
