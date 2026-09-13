"""Group repeated failures without a vector database.

Token Jaccard over already-sanitized significant tokens. No embeddings.
Does not trigger engineering work from a single production error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from services.improvement.records import FailureRecord
from services.workspace_files.file_resolver import significant_tokens_in_order

_SIMILARITY = 0.45


@dataclass
class FailureCluster:
    cluster_id: str
    failure_class: str
    component: str
    fingerprint: tuple[str, ...]
    members: list[str] = field(default_factory=list)
    frequency: int = 0
    severity: str = "medium"
    user_impact: str = ""
    recency: str = ""
    pattern_kind: str = "one-off"

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "failure_class": self.failure_class,
            "component": self.component,
            "fingerprint": list(self.fingerprint),
            "members": list(self.members),
            "frequency": self.frequency,
            "severity": self.severity,
            "user_impact": self.user_impact,
            "recency": self.recency,
            "pattern_kind": self.pattern_kind,
        }


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(significant_tokens_in_order(text or "", limit=12))


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cluster_id_for(record: FailureRecord) -> str:
    tokens = _tokens(record.input_reference)
    head = "-".join(tokens[:6]) or "none"
    return f"{record.failure_class}:{record.component}:{head}"


def assign_cluster(record: FailureRecord, existing: list[FailureCluster]) -> FailureCluster:
    tokens = _tokens(record.input_reference)
    for cluster in existing:
        if cluster.failure_class != record.failure_class:
            continue
        if cluster.component != record.component:
            continue
        if _jaccard(tokens, cluster.fingerprint) >= _SIMILARITY:
            cluster.members.append(record.failure_id)
            cluster.frequency += max(1, record.repeat_count)
            cluster.recency = record.timestamp or cluster.recency
            cluster.pattern_kind = _kind(cluster.frequency, len(cluster.members))
            return cluster
    cluster = FailureCluster(
        cluster_id=cluster_id_for(record),
        failure_class=record.failure_class,
        component=record.component,
        fingerprint=tokens,
        members=[record.failure_id],
        frequency=max(1, record.repeat_count),
        severity=record.severity,
        user_impact=record.user_visible_impact,
        recency=record.timestamp,
        pattern_kind=_kind(max(1, record.repeat_count), 1),
    )
    existing.append(cluster)
    return cluster


def _kind(frequency: int, distinct: int) -> str:
    if frequency >= 3 or distinct >= 3:
        return "systemic"
    if frequency >= 2 or distinct >= 2:
        return "repeated"
    return "one-off"


def should_open_engineering(cluster: FailureCluster) -> bool:
    """One-off production errors do not open an engineering loop by themselves."""
    if cluster.pattern_kind == "one-off" and cluster.frequency <= 1:
        return False
    return True
