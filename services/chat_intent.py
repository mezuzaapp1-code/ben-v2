"""Natural-language chat routing — explicit engine handoff and thread opinion intents."""
from __future__ import annotations

import re
from dataclasses import dataclass

from services.tier1_models import tier1_model_for

# Direct address at message start (Hey Claude / היי קלוד / Gemini, …)
_START_DIRECT_ADDRESS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:hey\s+)?claude\b", re.IGNORECASE), "claude"),
    (re.compile(r"^(?:hey\s+)?gemini\b", re.IGNORECASE), "gemini"),
    (re.compile(r"^(?:hey\s+)?(?:gpt|chatgpt)\b", re.IGNORECASE), "gpt"),
    (re.compile(r"^היי\s+קלוד\b"), "claude"),
    (re.compile(r"^קלוד\b", re.IGNORECASE), "claude"),
    (re.compile(r"^היי\s+ג['']?מיני\b|^היי\s+גמיני\b"), "gemini"),
    (re.compile(r"^ג['']?מיני\b|^גמיני\b", re.IGNORECASE), "gemini"),
)

# Named engine tokens for explicit routing directives anywhere in the message.
_NAMED_ENGINE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bclaude\b|\bקלוד\b", re.IGNORECASE), "claude"),
    (re.compile(r"\bgemini\b|\bג['']?מיני\b|\bגמיני\b", re.IGNORECASE), "gemini"),
    (re.compile(r"\b(?:gpt|chatgpt)\b", re.IGNORECASE), "gpt"),
)

_ROUTING_DIRECTIVE = re.compile(
    r"(?:"
    r"\b(?:please\s+)?(?:switch|route|hand\s*off|move)\s+(?:to|over\s+to)\s+(?:hey\s+)?(?:claude|gemini|gpt|chatgpt)\b"
    r"|\b(?:ask|tell|ping|use)\s+(?:hey\s+)?(?:claude|gemini|gpt|chatgpt)\b"
    r"|\b(?:עבור(?:\s+ל)?|החלף(?:\s+ל)?|תשאל(?:\s+את)?)\s+(?:קלוד|ג['']?מיני|גמיני)\b"
    r")",
    re.IGNORECASE,
)

_OPINION_ON_THREAD = re.compile(
    r"(?:"
    r"what\s+do\s+you\s+think(?:\s+about)?(?:\s+the)?\s*(?:conversation|discussion|chat|thread)?"
    r"|your\s+(?:expert\s+)?opinion\s+on(?:\s+the)?\s*(?:conversation|discussion|chat|thread)?"
    r"|מה\s+(?:את(?:ה|)\s+)?(?:חושב|דעת(?:ך|))(?:\s+(?:על|לגבי))?\s*(?:ה)?(?:שיחה|דיון|שיח)?"
    r"|(?:מה|what)\s+(?:do\s+you\s+)?think\s+about\s+(?:this|the)\s+(?:conversation|discussion|chat)"
    r")",
    re.IGNORECASE,
)

_OPINION_CUE = re.compile(
    r"(?:what\s+do\s+you\s+think|your\s+opinion|מה\s+(?:את(?:ה|)\s+)?(?:חושב|דעת(?:ך|)))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChatRoutingIntent:
    provider_id: str | None = None
    model_override: str | None = None
    expert_opinion: bool = False
    opinion_request: str | None = None


def _first_named_engine(text: str) -> str | None:
    for pattern, provider_id in _NAMED_ENGINE:
        if pattern.search(text):
            return provider_id
    return None


def detect_explicit_provider_routing(message: str) -> str | None:
    """Route only on direct address or explicit handoff — not casual mid-sentence mentions."""
    text = (message or "").strip()
    if not text:
        return None
    for pattern, provider_id in _START_DIRECT_ADDRESS:
        if pattern.match(text):
            return provider_id
    if _ROUTING_DIRECTIVE.search(text):
        return _first_named_engine(text)
    return None


def detect_mentioned_provider(message: str) -> str | None:
    """Backward-compatible alias for explicit routing detection."""
    return detect_explicit_provider_routing(message)


def is_cross_engine_turn(
    *,
    ui_provider_id: str | None,
    resolved_provider_id: str | None,
) -> bool:
    ui = (ui_provider_id or "").strip().lower()
    resolved = (resolved_provider_id or "").strip().lower()
    return bool(ui and resolved and ui != resolved)


def detect_thread_opinion_intent(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if _OPINION_ON_THREAD.search(text):
        return True
    mentioned = detect_explicit_provider_routing(text)
    return bool(mentioned and _OPINION_CUE.search(text))


def resolve_chat_intent(
    message: str,
    *,
    default_provider_id: str | None = None,
    default_model_override: str | None = None,
    explicit_expert_opinion: bool = False,
) -> ChatRoutingIntent:
    """Derive per-turn routing from natural language when not explicitly overridden."""
    if explicit_expert_opinion:
        provider = detect_explicit_provider_routing(message) or default_provider_id
        model = default_model_override or (tier1_model_for(provider) if provider else None)
        return ChatRoutingIntent(
            provider_id=provider,
            model_override=model or None,
            expert_opinion=True,
            opinion_request=(message or "").strip() or None,
        )

    mentioned = detect_explicit_provider_routing(message)
    wants_opinion = detect_thread_opinion_intent(message)

    if not mentioned and not wants_opinion:
        return ChatRoutingIntent()

    provider = mentioned or default_provider_id
    model = tier1_model_for(provider) if provider else default_model_override

    if wants_opinion:
        return ChatRoutingIntent(
            provider_id=provider,
            model_override=model or None,
            expert_opinion=True,
            opinion_request=(message or "").strip() or None,
        )

    if mentioned:
        return ChatRoutingIntent(
            provider_id=mentioned,
            model_override=tier1_model_for(mentioned),
            expert_opinion=False,
        )

    return ChatRoutingIntent()


def apply_chat_intent_to_request(
    message: str,
    *,
    provider_id: str | None,
    model_override: str | None,
    expert_opinion: bool,
) -> tuple[str | None, str | None, bool]:
    """Returns (provider_id, model_override, expert_opinion) after NL resolution."""
    intent = resolve_chat_intent(
        message,
        default_provider_id=provider_id,
        default_model_override=model_override,
        explicit_expert_opinion=expert_opinion,
    )
    resolved_provider = intent.provider_id or provider_id
    if intent.provider_id:
        resolved_model = intent.model_override or tier1_model_for(intent.provider_id) or model_override
    else:
        resolved_model = intent.model_override or model_override
    resolved_expert = expert_opinion or intent.expert_opinion
    return resolved_provider, resolved_model, resolved_expert
