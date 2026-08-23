"""Global chat system prompts and thread context composition."""
from __future__ import annotations

GLOBAL_CHAT_SYSTEM = (
    "The current year is 2026. You are in a direct 1:1 chat — not a committee, council, or synthesis pipeline. "
    "Answer naturally, clearly, and in the language of the user's current request. Match the response format to the request "
    "(lists, prose, code, etc.). Never impose scorecards, markdown tables, or numbered deployment playbooks "
    "unless the user explicitly asks for them. Never attribute answers to named experts or models. "
    "When conversation history is provided, use it as background only."
)


def compose_chat_user_message(*, conversation_history: str | None, user_text: str) -> str:
    """Merge prior thread turns with the live user message for /chat routes."""
    text = (user_text or "").strip()
    history = (conversation_history or "").strip()
    if not history:
        return text
    return (
        f"<conversation_history>\n{history}\n</conversation_history>\n\n"
        f"<user_message>\n{text}\n</user_message>"
    )
