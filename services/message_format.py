"""Encode/decode message content for chat and council rehydration (JSON envelope in Text)."""
from __future__ import annotations

import json
import uuid
from typing import Any

_BEN_PREFIX = '{"ben":'

# Large Paste V1 — conversation-scoped chat content (not WorkspaceFile).
LARGE_PASTE_THRESHOLD = 10_000
LARGE_PASTE_UNWRAP_CEILING = 25_000
PROVIDER_EXPANDED_MAX_CHARS = 400_000
USER_TURN_KIND = "user_turn"
LARGE_PASTE_DEFAULT_LABEL = "Pasted text"


def code_point_count(text: str) -> int:
    """Unicode code points. Python 3 str is already code-point indexed."""
    return len(text or "")


def format_char_count(n: int) -> str:
    return f"{int(n):,}"


def format_large_paste_stub(char_count: int) -> str:
    return f"[Large paste · {format_char_count(char_count)} characters]"


def format_file_ref_stub(name: str | None = None) -> str:
    label = " ".join(str(name or "image").split()).replace('"', "'")[:256] or "image"
    return f"[Attached image · {label}]"


def _sanitize_user_turn_parts(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        part_type = item.get("type")
        if part_type == "text":
            text = item.get("text")
            if not isinstance(text, str):
                return None
            out.append({"type": "text", "text": text})
            continue
        if part_type == "large_paste":
            text = item.get("text")
            if not isinstance(text, str):
                return None
            part_id = str(item.get("id") or "").strip()
            if not part_id:
                return None
            label = str(item.get("label") or LARGE_PASTE_DEFAULT_LABEL).strip() or LARGE_PASTE_DEFAULT_LABEL
            try:
                stored_count = item.get("char_count")
                char_count = int(stored_count) if stored_count is not None else code_point_count(text)
            except (TypeError, ValueError):
                return None
            # Body is the source of truth; never silently shrink stored text.
            char_count = code_point_count(text)
            out.append(
                {
                    "type": "large_paste",
                    "id": part_id,
                    "label": label,
                    "text": text,
                    "char_count": char_count,
                }
            )
            continue
        if part_type == "file_ref":
            raw_id = str(item.get("file_id") or "").strip()
            try:
                file_id = str(uuid.UUID(raw_id))
            except (TypeError, ValueError):
                continue
            name = " ".join(str(item.get("name") or "image").split()).replace('"', "'")[:256] or "image"
            out.append({"type": "file_ref", "file_id": file_id, "name": name})
            continue
        return None
    return out


def parse_user_turn_parts(content: str) -> list[dict[str, Any]] | None:
    """Return ordered parts only for a valid ben=1 user_turn envelope."""
    if not isinstance(content, str) or not content.startswith(_BEN_PREFIX):
        return None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("ben") != 1 or data.get("kind") != USER_TURN_KIND:
        return None
    return _sanitize_user_turn_parts(data.get("parts"))


def user_turn_has_file_refs(content: str) -> bool:
    parts = parse_user_turn_parts(content)
    if not parts:
        return False
    return any(part.get("type") == "file_ref" for part in parts)


def display_text_from_parts(parts: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for part in parts:
        if part.get("type") == "text":
            chunks.append(str(part.get("text") or ""))
        elif part.get("type") == "large_paste":
            count = part.get("char_count")
            if count is None:
                count = code_point_count(str(part.get("text") or ""))
            chunks.append(format_large_paste_stub(int(count)))
        elif part.get("type") == "file_ref":
            chunks.append(format_file_ref_stub(str(part.get("name") or "image")))
    return "".join(chunks)


def expand_parts_for_provider(parts: list[dict[str, Any]]) -> str:
    """Text/large_paste bodies only. Image bytes are never inlined as text."""
    return "".join(
        str(part.get("text") or "")
        for part in parts
        if part.get("type") in {"text", "large_paste"}
    )


def encode_user_turn(parts: list[dict[str, Any]]) -> str:
    """Persist ordered parts. Text-only turns stay raw strings (legacy)."""
    sanitized = _sanitize_user_turn_parts(parts)
    if sanitized is None:
        return ""
    compact = [
        part
        for part in sanitized
        if part.get("type") in {"large_paste", "file_ref"}
        or (part.get("type") == "text" and part.get("text") != "")
    ]
    if not compact:
        return ""
    if not any(part.get("type") in {"large_paste", "file_ref"} for part in compact):
        return "".join(str(part.get("text") or "") for part in compact if part.get("type") == "text")
    return json.dumps({"ben": 1, "kind": USER_TURN_KIND, "parts": compact}, ensure_ascii=False)


def expand_user_message_for_provider(content: str) -> str:
    """Current-turn expansion: full Large Paste bodies, exact part order."""
    parts = parse_user_turn_parts(content)
    if parts is None:
        return content
    return expand_parts_for_provider(parts)


def user_turn_instruction_text(content: str) -> str:
    """Usable inline instruction text only — never the paste body."""
    parts = parse_user_turn_parts(content)
    if parts is None:
        return content
    return "".join(str(part.get("text") or "") for part in parts if part.get("type") == "text")


def user_turn_focus_query_source(content: str) -> str:
    """Focus query source: instruction text, or a bounded paste-only stub."""
    parts = parse_user_turn_parts(content)
    if parts is None:
        return content
    instruction = "".join(str(part.get("text") or "") for part in parts if part.get("type") == "text").strip()
    if instruction:
        return instruction
    stubs = [
        format_large_paste_stub(int(part.get("char_count") or code_point_count(str(part.get("text") or ""))))
        for part in parts
        if part.get("type") == "large_paste"
    ]
    return " ".join(stubs)


def user_turn_copilot_intent_source(content: str) -> str:
    """Copilot intent surface: typed instruction or bounded stub — never large_paste body.

    Explicit commands such as "@intel check this site" still reach Copilot when they
    are instruction text. Incidental words inside a Large Paste do not.
    """
    return user_turn_focus_query_source(content)


def provider_expansion_too_large(expanded: str) -> str | None:
    n = code_point_count(expanded)
    if n <= PROVIDER_EXPANDED_MAX_CHARS:
        return None
    return (
        f"This message is {format_char_count(n)} characters. BEN will not send more than "
        f"{format_char_count(PROVIDER_EXPANDED_MAX_CHARS)} characters in one request. "
        "This is a BEN transport limit, not a guarantee that the selected model can fit the content. "
        "The Large Paste was not truncated and remains recoverable."
    )


def thread_title_from_user_message(content: str) -> str:
    decoded = decode_message("user", content)
    display = str(decoded.get("content") or content).strip()
    return (display[:512] or "Chat")[:512]

from services.providers.speaking_registry import (
    gateway_to_provider_id as _registry_gateway_to_provider_id,
)
from services.providers.speaking_registry import (
    provider_label as _registry_provider_label,
)
from services.synthesis_delivery import build_product_delivery_display, enforce_delivery_shape


def gateway_to_provider_id(provider_used: str) -> str:
    return _registry_gateway_to_provider_id(provider_used)


def provider_display_label(provider_id: str) -> str:
    return _registry_provider_label(provider_id)


def _sanitize_used_files(raw: Any) -> list[dict[str, str]]:
    """Only persist/restore backend-injected {id, name}. Never infer filenames."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("id") or "").strip()
        name = " ".join(str(item.get("name") or "").split()).replace('"', "'")[:256]
        if not file_id or not name or file_id in seen:
            continue
        seen.add(file_id)
        out.append({"id": file_id, "name": name})
    return out


def _sanitize_unavailable_count(raw: Any) -> int:
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


def encode_chat_assistant(
    text: str,
    *,
    model_used: str = "",
    cost_usd: float = 0.0,
    provider_id: str = "",
    provider_used: str = "",
    used_files: Any = None,
    unavailable_count: Any = None,
) -> str:
    clean_used = _sanitize_used_files(used_files)
    clean_unavailable = _sanitize_unavailable_count(unavailable_count)
    if (
        not model_used
        and not cost_usd
        and not provider_id
        and not provider_used
        and not clean_used
        and not clean_unavailable
    ):
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
    if clean_used:
        payload["used_files"] = clean_used
    if clean_unavailable:
        payload["unavailable_count"] = clean_unavailable
    return json.dumps(payload, ensure_ascii=False)


COUNCIL_DISPLAY_LABEL = {
    "Legal Advisor": "⚖️ Legal Advisor",
    "Business Advisor": "💼 Business Advisor",
    "Strategy Advisor": "🎯 Strategy Advisor",
    "Local Codebase Expert": "🧩 Local Codebase Expert",
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
        parts = parse_user_turn_parts(content)
        if parts is None:
            return {"role": "user", "content": content}
        return {
            "role": "user",
            "kind": USER_TURN_KIND,
            "content": display_text_from_parts(parts),
            "parts": parts,
        }

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
                "used_files": _sanitize_used_files(data.get("used_files")),
                "unavailable_count": _sanitize_unavailable_count(data.get("unavailable_count")),
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
    available = int(synthesis.get("available_experts") or 0)
    mode = str(synthesis.get("synthesis_mode") or "").strip().lower()

    if available == 0 or mode == "degraded":
        return (
            "Council unavailable: no experts responded.\n\n"
            f"{synthesis.get('recommendation') or ''}\n\n"
            "Retry when experts are available."
        )

    delivery = build_product_delivery_display(synthesis)
    if delivery:
        return delivery

    shaped = enforce_delivery_shape(
        {
            "recommendation": synthesis.get("recommendation") or "Fused deliverable",
            "deliverable_artifact": synthesis.get("deliverable_artifact") or "",
            "operational_playbook": synthesis.get("operational_playbook") or [],
        }
    )
    return build_product_delivery_display(shaped) or str(synthesis.get("recommendation") or "")


def build_adhoc_synthesis_display_text(
    synthesis: dict[str, Any],
    *,
    locale: str = "en",
    any_expert_failed: bool = False,
) -> str:
    _ = locale
    return build_synthesis_display_text(synthesis, any_expert_failed=any_expert_failed)
