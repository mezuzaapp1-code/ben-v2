"""Encode/decode message content for chat and council rehydration (JSON envelope in Text)."""
from __future__ import annotations

import json
from typing import Any

_BEN_PREFIX = '{"ben":'

GATEWAY_TO_PROVIDER_ID = {"openai": "gpt", "anthropic": "claude", "google": "gemini"}
PROVIDER_ID_LABELS = {"gpt": "GPT", "claude": "Claude", "gemini": "Gemini"}


def gateway_to_provider_id(provider_used: str) -> str:
    return GATEWAY_TO_PROVIDER_ID.get((provider_used or "").strip().lower(), "")


def provider_display_label(provider_id: str) -> str:
    return PROVIDER_ID_LABELS.get((provider_id or "").strip().lower(), "")


def encode_chat_assistant(
    text: str,
    *,
    model_used: str = "",
    cost_usd: float = 0.0,
    provider_id: str = "",
    provider_used: str = "",
) -> str:
    if not model_used and not cost_usd and not provider_id and not provider_used:
        return text
    payload: dict[str, Any] = {
        "ben": 1,
        "kind": "chat",
        "text": text,
        "model_used": model_used,
        "cost_usd": cost_usd,
    }
    if provider_id:
        payload["provider_id"] = provider_id
    if provider_used:
        payload["provider_used"] = provider_used
    return json.dumps(payload, ensure_ascii=False)


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
    room_id: str | None = None,
    question_id: str | None = None,
    expert_index: int | None = None,
) -> str:
    head = COUNCIL_DISPLAY_LABEL.get(expert, expert)
    payload: dict[str, Any] = {
        "ben": 1,
        "kind": "council_expert",
        "expert": expert,
        "response": response,
        "display_content": f"{head}: {response}",
        "provider": provider,
        "model": model,
        "outcome": outcome,
        "cost_usd": cost_usd,
    }
    if room_id:
        payload["room_id"] = room_id
    if question_id:
        payload["question_id"] = question_id
    if expert_index is not None:
        payload["expert_index"] = expert_index
    return json.dumps(payload, ensure_ascii=False)


def encode_council_synthesis(
    *,
    synthesis: dict[str, Any],
    cost_usd: float,
    display_text: str,
    room_id: str | None = None,
    question_id: str | None = None,
    room_status: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "ben": 1,
        "kind": "council_synthesis",
        "synthesis": synthesis,
        "cost_usd": cost_usd,
        "display_text": display_text,
    }
    if room_id:
        payload["room_id"] = room_id
    if question_id:
        payload["question_id"] = question_id
    if room_status:
        payload["room_status"] = room_status
    return json.dumps(payload, ensure_ascii=False)


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
                "provider_id": data.get("provider_id") or "",
                "provider_used": data.get("provider_used") or "",
            }
        if kind == "council_expert":
            expert = data.get("expert") or "Advisor"
            resp = data.get("response") or ""
            outcome = data.get("outcome") or "ok"
            label = _expert_status_from_outcome(outcome, resp)
            display = data.get("display_content") or f"{expert}: {resp}"
            out: dict[str, Any] = {
                "role": "assistant",
                "content": display,
                "model_used": data.get("model") or "",
                "expert_outcome": outcome,
                "expert_status": label,
                "cost_usd": float(data.get("cost_usd") or 0),
            }
            if data.get("room_id"):
                out["room_id"] = data.get("room_id")
            if data.get("question_id"):
                out["question_id"] = data.get("question_id")
            if data.get("expert_index") is not None:
                out["expert_index"] = data.get("expert_index")
            return out
        if kind == "council_synthesis":
            syn = data.get("synthesis")
            if isinstance(syn, dict):
                out_syn: dict[str, Any] = {
                    "role": "assistant",
                    "kind": "council_synthesis",
                    "synthesis": syn,
                    "content": str(data.get("display_text") or ""),
                    "model_used": "synthesis",
                    "cost_usd": float(data.get("cost_usd") or 0),
                }
                if data.get("room_id"):
                    out_syn["room_id"] = data.get("room_id")
                if data.get("question_id"):
                    out_syn["question_id"] = data.get("question_id")
                if data.get("room_status"):
                    out_syn["room_status"] = data.get("room_status")
                return out_syn

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
