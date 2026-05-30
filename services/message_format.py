"""Encode/decode message content for chat and council rehydration (JSON envelope in Text)."""
from __future__ import annotations

import json
from typing import Any

_BEN_PREFIX = '{"ben":'

from services.providers.speaking_registry import (
    gateway_to_provider_id as _registry_gateway_to_provider_id,
)
from services.providers.speaking_registry import (
    provider_label as _registry_provider_label,
)


def gateway_to_provider_id(provider_used: str) -> str:
    return _registry_gateway_to_provider_id(provider_used)


def provider_display_label(provider_id: str) -> str:
    return _registry_provider_label(provider_id)


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


def build_adhoc_expert_display_text(
    provider_id: str,
    model: str,
    response: str,
) -> str:
    """Provider-first label for ad-hoc expert bubbles (rehydration + persist)."""
    label = provider_display_label(provider_id) or (provider_id or "Model").strip()
    model_s = (model or "").strip()
    head = f"{label} · {model_s}" if model_s else label
    return f"{head}: {response}"


def encode_adhoc_expert(
    *,
    session_id: str,
    provider_id: str,
    response: str,
    provider_used: str = "",
    model: str = "",
    outcome: str = "ok",
    cost_usd: float = 0.0,
    sequence: int | None = None,
    display_content: str | None = None,
) -> str:
    display = display_content or build_adhoc_expert_display_text(provider_id, model, response)
    payload: dict[str, Any] = {
        "ben": 1,
        "kind": "adhoc_expert",
        "session_id": session_id,
        "provider_id": provider_id,
        "response": response,
        "display_content": display,
        "outcome": outcome,
        "cost_usd": cost_usd,
    }
    if provider_used:
        payload["provider_used"] = provider_used
    if model:
        payload["model"] = model
    if sequence is not None:
        payload["sequence"] = sequence
    return json.dumps(payload, ensure_ascii=False)


def encode_adhoc_synthesis(
    *,
    session_id: str,
    synthesis: dict[str, Any],
    display_text: str,
    cost_usd: float = 0.0,
) -> str:
    payload: dict[str, Any] = {
        "ben": 1,
        "kind": "adhoc_synthesis",
        "session_id": session_id,
        "synthesis": synthesis,
        "display_text": display_text,
        "cost_usd": cost_usd,
    }
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
        if kind == "adhoc_expert":
            provider_id = str(data.get("provider_id") or "")
            resp = str(data.get("response") or "")
            outcome = str(data.get("outcome") or "ok")
            model = str(data.get("model") or "")
            display = data.get("display_content") or build_adhoc_expert_display_text(
                provider_id, model, resp
            )
            label = _expert_status_from_outcome(outcome, resp)
            out_adhoc: dict[str, Any] = {
                "role": "assistant",
                "kind": "adhoc_expert",
                "content": display,
                "provider_id": provider_id,
                "provider_used": data.get("provider_used") or "",
                "model_used": model,
                "expert_outcome": outcome,
                "expert_status": label,
                "cost_usd": float(data.get("cost_usd") or 0),
                "adhoc_session_id": str(data.get("session_id") or ""),
            }
            if data.get("sequence") is not None:
                out_adhoc["sequence"] = int(data.get("sequence"))
            return out_adhoc
        if kind == "adhoc_synthesis":
            syn = data.get("synthesis")
            if isinstance(syn, dict):
                out_adhoc_syn: dict[str, Any] = {
                    "role": "assistant",
                    "kind": "adhoc_synthesis",
                    "synthesis": syn,
                    "content": str(data.get("display_text") or ""),
                    "model_used": "synthesis",
                    "cost_usd": float(data.get("cost_usd") or 0),
                    "adhoc_session_id": str(data.get("session_id") or ""),
                }
                return out_adhoc_syn

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
    available = int(synthesis.get("available_experts") or 0)
    mode = str(synthesis.get("synthesis_mode") or "").strip().lower()
    footer = "This is a structured reasoning layer, not a final answer."

    if available == 0 or mode == "degraded":
        return (
            "Council unavailable: no experts responded.\n\n"
            f"{rec}\n\n"
            f"{footer}"
        )

    if available == 1 or mode == "single_expert":
        return (
            "Single expert result — no consensus available.\n\n"
            f"🧠 BEN Synthesis ({ae})\n{rec}\n\n"
            f"{footer}"
        )

    if available == 2:
        prefix = "Partial council (2 experts) — limited consensus.\n\n"
        consensus_block = f"⚡ Limited consensus: {cons}\n" if cons else ""
        return (
            f"{prefix}🧠 BEN Synthesis ({ae})\n{rec}\n\n"
            f"{consensus_block}⚡ Disagreement: {disagree_s}\n\n"
            f"{footer}"
        )

    prefix = "Based on available expert responses.\n\n" if any_expert_failed else ""
    return (
        f"{prefix}🧠 BEN Synthesis ({ae})\n{rec}\n\n"
        f"✅ Consensus: {cons}\n⚡ Disagreement: {disagree_s}\n\n"
        f"{footer}"
    )
