"""Tests for natural-language chat routing intents."""

from services.chat_intent import (
    apply_chat_intent_to_request,
    detect_mentioned_provider,
    detect_thread_opinion_intent,
    resolve_chat_intent,
    tier1_model_for,
)


def test_detect_claude_hebrew():
    assert detect_mentioned_provider("היי קלוד") == "claude"


def test_detect_claude_english():
    assert detect_mentioned_provider("Hey Claude, summarize this") == "claude"


def test_detect_gemini_direct_address():
    assert detect_mentioned_provider("Gemini what do you think?") == "gemini"


def test_casual_gemini_mention_does_not_route():
    assert detect_mentioned_provider("I compared Gemini and Claude yesterday") is None
    assert detect_mentioned_provider("The gemini API docs mention rate limits") is None


def test_explicit_routing_directive():
    assert detect_mentioned_provider("Please switch to Claude for this answer") == "claude"
    assert detect_mentioned_provider("Can you ask Gemini to review the plan?") == "gemini"


def test_thread_opinion_hebrew():
    assert detect_thread_opinion_intent("מה אתה חושב על השיחה") is True


def test_thread_opinion_english():
    assert detect_thread_opinion_intent("What do you think about the conversation?") is True


def test_provider_only_not_opinion():
    assert detect_thread_opinion_intent("Hey Claude") is False


def test_resolve_opinion_routes_tier1_claude():
    intent = resolve_chat_intent("היי קלוד מה דעתך על השיחה", default_provider_id="gpt")
    assert intent.provider_id == "claude"
    assert intent.expert_opinion is True
    assert intent.model_override == tier1_model_for("claude")


def test_apply_intent_keeps_explicit_expert_flag():
    provider, model, expert = apply_chat_intent_to_request(
        "follow up",
        provider_id="gpt",
        model_override="gpt-4o-mini",
        expert_opinion=True,
    )
    assert expert is True
    assert provider == "gpt"


def test_apply_intent_keeps_ui_provider_on_casual_mention():
    provider, model, expert = apply_chat_intent_to_request(
        "I read that Gemini handles images well",
        provider_id="gpt",
        model_override="gpt-4o",
        expert_opinion=False,
    )
    assert provider == "gpt"
    assert model == "gpt-4o"
    assert expert is False


def test_apply_intent_routes_named_engine_over_stale_override():
    provider, model, expert = apply_chat_intent_to_request(
        "Hey Gemini, draft a checklist",
        provider_id="gpt",
        model_override="gemini-1.5-pro",
        expert_opinion=False,
    )
    assert provider == "gemini"
    assert model == tier1_model_for("gemini")
    assert model == "gemini-3.5-flash"
    assert expert is False


def test_resolve_opinion_uses_tier1_for_active_gemini():
    intent = resolve_chat_intent(
        "מה אתה חושב על השיחה",
        default_provider_id="gemini",
        default_model_override="gemini-1.5-pro",
    )
    assert intent.expert_opinion is True
    assert intent.model_override == "gemini-3.5-flash"
