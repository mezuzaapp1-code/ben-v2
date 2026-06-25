"""Parse and render fused product-delivery synthesis payloads."""
from __future__ import annotations

import re
from typing import Any

FORBIDDEN_FUSION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(expert\s*\d+|claude|gemini|gpt|anthropic|openai|google)\s+(says|suggests|recommends|argues|notes)\b",
        re.I,
    ),
    re.compile(r"\bpoints?\s+of\s+(agreement|disagreement)\b", re.I),
    re.compile(r"\bno\s+disagreements?\b", re.I),
    re.compile(r"\bconsensus\s+(points?|summary|view|label)\b", re.I),
    re.compile(r"\b(main\s+)?disagreement(s)?\b", re.I),
    re.compile(r"\b(experts?\s+(align|agree|diverge|disagree))\b", re.I),
    re.compile(r"\b(legal|business|strategy)\s+advisor\b", re.I),
)

ADVISORY_METADATA_KEYS: tuple[str, ...] = (
    "consensus_points",
    "main_disagreement",
    "disagreement_points",
    "shared_recommendation",
    "legal_reasoning",
    "operational_reasoning",
    "strategic_reasoning",
    "infrastructure_reasoning",
    "minority_or_unique_views",
)


def sanitize_fused_text(text: str) -> str:
    """Remove committee/meta-analysis phrasing from user-facing fused content."""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    for pattern in FORBIDDEN_FUSION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def strip_advisory_metadata(synthesis: dict[str, Any]) -> dict[str, Any]:
    """Drop secretary-style fields; keep only the fused product payload."""
    out = dict(synthesis)
    for key in ADVISORY_METADATA_KEYS:
        out.pop(key, None)
    return out


def _ensure_scorecard_structure(artifact: str, recommendation: str) -> str:
    art = sanitize_fused_text(artifact)
    if art and ("## Executive Scorecard" in art or "| Dimension |" in art):
        return art
    summary = sanitize_fused_text(recommendation or art or "Consolidated council output")
    body = art or summary
    return sanitize_fused_text(
        f"## Executive Scorecard\n\n"
        f"| Dimension | Status | Notes |\n"
        f"|-----------|--------|-------|\n"
        f"| Primary deliverable | Ready | {summary[:400]} |\n\n"
        f"## Production Artifact\n\n{body}"
    )


def norm_operational_playbook(val: Any) -> list[dict[str, Any]]:
    if not isinstance(val, list):
        return []
    out: list[dict[str, Any]] = []
    for item in val[:3]:
        if not isinstance(item, dict):
            continue
        command = sanitize_fused_text(str(item.get("command") or ""))
        if not command:
            continue
        try:
            step = int(item.get("step", len(out) + 1))
        except (TypeError, ValueError):
            step = len(out) + 1
        purpose = sanitize_fused_text(str(item.get("purpose") or item.get("traceable_to") or ""))
        out.append({"step": step, "command": command, "purpose": purpose})
    return out


def format_operational_playbook(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return ""
    lines: list[str] = []
    for item in steps[:3]:
        step = item.get("step", len(lines) + 1)
        command = str(item.get("command") or "").strip()
        purpose = str(item.get("purpose") or "").strip()
        if not command:
            continue
        line = f"{step}. `{command}`"
        if purpose:
            line += f" — {purpose}"
        lines.append(line)
    return "\n".join(lines)


def build_product_delivery_display(synthesis: dict[str, Any]) -> str | None:
    artifact = str(synthesis.get("deliverable_artifact") or "").strip()
    if not artifact:
        return None
    playbook = norm_operational_playbook(
        synthesis.get("operational_playbook") or synthesis.get("next_steps")
    )
    parts = [f"📦 Deliverable\n\n{artifact}"]
    formatted = format_operational_playbook(playbook)
    if formatted:
        parts.append(f"\n🚀 Work Plan\n\n{formatted}")
    return "\n".join(parts)


def enforce_delivery_shape(synthesis: dict[str, Any]) -> dict[str, Any]:
    """Enforce fused scorecard + exactly 3 playbook steps; strip advisory filler."""
    out = strip_advisory_metadata(dict(synthesis))
    rec = sanitize_fused_text(str(out.get("recommendation") or "").strip())
    artifact = str(out.get("deliverable_artifact") or "").strip()
    out["deliverable_artifact"] = _ensure_scorecard_structure(artifact, rec)
    playbook = norm_operational_playbook(out.get("operational_playbook") or out.get("next_steps"))
    while len(playbook) < 3:
        playbook.append(
            {
                "step": len(playbook) + 1,
                "command": "Review deliverable above and apply in target environment",
                "purpose": "Confirm fused output matches request",
            }
        )
    out["operational_playbook"] = playbook[:3]
    out["next_steps"] = [
        {
            "priority": p["step"],
            "command": p["command"],
            "traceable_to": p.get("purpose") or f"work plan step {p['step']}",
        }
        for p in out["operational_playbook"]
    ]
    if not rec:
        out["recommendation"] = "Fused deliverable"
    else:
        out["recommendation"] = rec
    return out
