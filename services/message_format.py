"""Encode/decode message content for chat and council rehydration (JSON envelope in Text)."""
from __future__ import annotations

import json
from typing import Any

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
            label = _expert_status_from_outcome(outcome, resp)
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


def _expert_status_from_outcome(outcome: str, response: str) -> str | None:
    if not outcome or outcome == "ok":
        return None
    if outcome == "timeout":
        return "Unavailable: timeout"
    import re

    m = re.search(r"Expert unavailable \(([^)]+)\)", response or "")
    if outcome == "degraded" and m:
        return f"Degraded: {m.group(1)}"
    if outcome == "error":
        return "Degraded: error"
    return f"Degraded: {outcome}"


def build_synthesis_display_text(synthesis: dict[str, Any], *, any_expert_failed: bool) -> str:
    disagree = synthesis.get("main_disagreement")
    disagree_s = str(disagree).strip() if disagree is not None and str(disagree).strip() else "None"
    ae = synthesis.get("agreement_estimate") or "unknown"
    rec = synthesis.get("recommendation") or ""
    cons = synthesis.get("consensus_points") or ""
    prefix = "Based on available expert responses.\n\n" if any_expert_failed else ""
    return (
        f"{prefix}🧠 BEN Synthesis ({ae})\n{rec}\n\n"
        f"✅ Consensus: {cons}\n⚡ Disagreement: {disagree_s}\n\n"
        "This is a structured reasoning layer, not a final answer."
    )
