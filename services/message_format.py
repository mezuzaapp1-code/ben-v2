"""Encode/decode message content for chat and council rehydration (JSON envelope in Text)."""
from __future__ import annotations

import json
from typing import Any

from services.language_context import (
    LanguageContext,
    detect_dominant_language,
    expert_status_label,
    is_meaningful_reasoning_value,
    label,
)

_BEN_PREFIX = '{"ben":'


def encode_chat_assistant(text: str, *, model_used: str = "", cost_usd: float = 0.0) -> str:
    if not model_used and not cost_usd:
        return text
    return json.dumps(
        {"ben": 1, "kind": "chat", "text": text, "model_used": model_used, "cost_usd": cost_usd},
        ensure_ascii=False,
    )


COUNCIL_DISPLAY_LABEL = {
    "Legal Advisor": "⚖️ Legal Advisor",
    "Business Advisor": "💼 Business Advisor",
    "Strategy Advisor": "🎯 Strategy Advisor",
}


def encode_council_expert(
    *,
    expert: str,
    response: str,
    provider: str,
    model: str,
    outcome: str,
    cost_usd: float = 0.0,
) -> str:
    head = COUNCIL_DISPLAY_LABEL.get(expert, expert)
    return json.dumps(
        {
            "ben": 1,
            "kind": "council_expert",
            "expert": expert,
            "response": response,
            "display_content": f"{head}: {response}",
            "provider": provider,
            "model": model,
            "outcome": outcome,
            "cost_usd": cost_usd,
        },
        ensure_ascii=False,
    )


def encode_council_synthesis(*, synthesis: dict[str, Any], cost_usd: float, display_text: str) -> str:
    return json.dumps(
        {
            "ben": 1,
            "kind": "council_synthesis",
            "synthesis": synthesis,
            "cost_usd": cost_usd,
            "display_text": display_text,
        },
        ensure_ascii=False,
    )


def decode_message(role: str, content: str) -> dict[str, Any]:
    """Map DB row to API/UI message shape."""
    if role == "user":
        return {"role": "user", "content": content}

    if content.startswith(_BEN_PREFIX):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return {"role": "assistant", "content": content}
        if not isinstance(data, dict) or data.get("ben") != 1:
            return {"role": "assistant", "content": content}
        kind = data.get("kind")
        if kind == "chat":
            return {
                "role": "assistant",
                "content": str(data.get("text", "")),
                "model_used": data.get("model_used") or "",
                "cost_usd": float(data.get("cost_usd") or 0),
            }
        if kind == "council_expert":
            expert = data.get("expert") or "Advisor"
            resp = data.get("response") or ""
            outcome = data.get("outcome") or "ok"
            label = expert_status_label(detect_dominant_language(resp), outcome, resp)
            display = data.get("display_content") or f"{expert}: {resp}"
            return {
                "role": "assistant",
                "content": display,
                "model_used": data.get("model") or "",
                "expert_outcome": outcome,
                "expert_status": label,
                "cost_usd": float(data.get("cost_usd") or 0),
            }
        if kind == "council_synthesis":
            syn = data.get("synthesis")
            if isinstance(syn, dict):
                return {
                    "role": "assistant",
                    "kind": "council_synthesis",
                    "synthesis": syn,
                    "content": str(data.get("display_text") or ""),
                    "model_used": "synthesis",
                    "cost_usd": float(data.get("cost_usd") or 0),
                }

    return {"role": "assistant", "content": content}


def build_synthesis_display_text(
    synthesis: dict[str, Any],
    *,
    any_expert_failed: bool,
    lang_ctx: LanguageContext | None = None,
) -> str:
    ctx = lang_ctx or detect_dominant_language("")
    disagree = synthesis.get("main_disagreement")
    if is_meaningful_reasoning_value(disagree):
        disagree_s = str(disagree).strip()
    else:
        disagree_s = label(ctx, "disagreement_none")
    ae = synthesis.get("agreement_estimate") or "unknown"
    rec = synthesis.get("recommendation") or ""
    cons = synthesis.get("consensus_points") or ""
    prefix = f"{label(ctx, 'synthesis_prefix_degraded')}\n\n" if any_expert_failed else ""
    title = label(ctx, "synthesis_title", ae=ae)
    return (
        f"{prefix}🧠 {title}\n{rec}\n\n"
        f"✅ {label(ctx, 'consensus')}: {cons}\n"
        f"⚡ {label(ctx, 'disagreement')}: {disagree_s}\n\n"
        f"{label(ctx, 'synthesis_footer')}"
    )


def prune_empty_synthesis_fields(synthesis: dict[str, Any]) -> dict[str, Any]:
    """Drop optional reasoning keys that are empty/null so clients do not render placeholders."""
    out = dict(synthesis)
    optional = (
        "shared_recommendation",
        "disagreement_points",
        "legal_reasoning",
        "operational_reasoning",
        "strategic_reasoning",
        "infrastructure_reasoning",
        "minority_or_unique_views",
    )
    for key in optional:
        if key in out and not is_meaningful_reasoning_value(out.get(key)):
            out.pop(key, None)
    md = out.get("main_disagreement")
    if not is_meaningful_reasoning_value(md):
        out["main_disagreement"] = None
    return out
