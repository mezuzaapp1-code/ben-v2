"""Shared language fidelity: current-turn instruction drives the system prompt."""
from __future__ import annotations

import os
import uuid
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from services.chat_language import (
    CURRENT_TURN_LANGUAGE_RULE,
    assemble_chat_system,
    detect_language_code,
    resolve_response_language,
)
from services.chat_prompt import GLOBAL_CHAT_SYSTEM
from services.chat_service import handle_chat, stream_chat_response
from services.message_format import encode_user_turn
from services.ops.runtime_diagnostics import detect_dominant_language

_HE = "זהו הסבר מפורט על הנושא בשפה העברית לצורכי בדיקה של מערכת הזיהוי האוטומטי."
_EN = "This is an English paragraph that explains the topic in enough detail for language detection."
_PROVIDERS = (
    ("gpt", "openai"),
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("grok", "xai"),
)


def test_dominant_language_is_diagnostic_only():
    assert detect_dominant_language(_HE) == "he"
    import services.chat_language as lang

    source = open(lang.__file__, encoding="utf-8").read()
    assert "detect_dominant_language" not in source
    assert "dominant_language" not in source


def test_history_and_english_paste_do_not_override_hebrew_instruction():
    assembled = (
        "<conversation_history>\nPrior English assistant reply about APIs.\n</conversation_history>\n\n"
        f"<user_message>\n{_HE}\n</user_message>"
    )
    assert detect_language_code(assembled) == "he"
    assert resolve_response_language(assembled, None) == "he"


def test_quoted_english_does_not_switch_hebrew_instruction():
    msg = f'{_HE} "This source document is entirely in English and very long."'
    assert detect_language_code(msg) == "he"


def test_large_paste_body_does_not_override_hebrew_instruction():
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "תסכם את המסמך"},
            {
                "type": "large_paste",
                "id": "p1",
                "label": "Pasted text",
                "text": _EN * 80,
                "char_count": len(_EN * 80),
            },
        ]
    )
    assert detect_language_code(encoded) == "he"
    assert detect_language_code(_EN * 80) == "en"
    system = assemble_chat_system(encoded, None)
    assert "Respond in Hebrew" in system
    assert _EN not in system
    assert "answer in english" not in encoded.lower() or "Respond in English" not in system


def test_paste_override_phrase_inside_body_is_ignored():
    encoded = encode_user_turn(
        [
            {"type": "text", "text": _HE},
            {
                "type": "large_paste",
                "id": "p2",
                "label": "Pasted text",
                "text": "please answer in English " + (_EN * 20),
                "char_count": len("please answer in English " + (_EN * 20)),
            },
        ]
    )
    assert resolve_response_language(encoded, None) == "he"


def test_explicit_override_still_wins():
    assert resolve_response_language(f"{_HE} answer in English", None) == "en"
    assert resolve_response_language(f"{_EN} בעברית", None) == "he"


def test_assemble_chat_system_is_shared_not_provider_specific():
    system = assemble_chat_system(_HE, None)
    assert "current request" in GLOBAL_CHAT_SYSTEM
    assert CURRENT_TURN_LANGUAGE_RULE in system
    assert "Respond in Hebrew" in system
    assert "openai" not in system.lower()
    assert "anthropic" not in system.lower()
    assert "gemini" not in system.lower()
    assert "xai" not in system.lower()
    assert "grok" not in system.lower()


@contextmanager
def _stream_patches(tid, fake_stream, *, history_text: str | None = None):
    async def ctx(_org, _tid, live):
        if history_text:
            return (
                f"<conversation_history>\n{history_text}\n</conversation_history>\n\n"
                f"<user_message>\n{live}\n</user_message>"
            )
        return live

    with ExitStack() as stack:
        stack.enter_context(patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)))
        stack.enter_context(patch("services.chat_service.is_project_setup_thread", return_value=False))
        stack.enter_context(
            patch("services.chat_service.build_chat_message_with_thread_context", new=AsyncMock(side_effect=ctx))
        )
        stack.enter_context(
            patch("services.chat_service.inject_knowledge_few_shot", new=AsyncMock(side_effect=lambda _m, p: p))
        )
        stack.enter_context(patch("services.chat_service.route_request_stream", side_effect=fake_stream))
        stack.enter_context(patch("services.chat_service.persist_chat_exchange_sqlite", return_value=(1, 2)))
        stack.enter_context(patch("services.chat_service._schedule_chat_persist"))
        stack.enter_context(patch("services.chat_service.run_copilot_preamble", new=AsyncMock(return_value=[])))
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id,gateway", _PROVIDERS)
async def test_stream_hebrew_sets_shared_system_for_each_provider(provider_id, gateway):
    org = uuid.uuid4()
    tid = uuid.uuid4()
    seen: dict = {}

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        seen["message"] = message
        seen["system"] = system
        seen["provider_id"] = provider_id
        yield ("ok", f"{provider_id}-model", gateway)

    with _stream_patches(tid, fake_stream):
        async for _ in stream_chat_response(
            _HE, "u", str(org), "free", thread_id=tid, provider_id=provider_id
        ):
            pass

    assert seen["provider_id"] == provider_id
    assert seen["message"] == _HE
    assert "Respond in Hebrew" in seen["system"]
    assert CURRENT_TURN_LANGUAGE_RULE in seen["system"]
    assert "Language preference" not in seen["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id,gateway", _PROVIDERS)
async def test_stream_english_sets_shared_system_for_each_provider(provider_id, gateway):
    org = uuid.uuid4()
    tid = uuid.uuid4()
    seen: dict = {}

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        seen["system"] = system
        seen["message"] = message
        yield ("ok", f"{provider_id}-model", gateway)

    with _stream_patches(tid, fake_stream):
        async for _ in stream_chat_response(
            _EN, "u", str(org), "free", thread_id=tid, provider_id=provider_id
        ):
            pass

    assert seen["message"] == _EN
    assert "Respond in English" in seen["system"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id,gateway", _PROVIDERS)
async def test_stream_hebrew_with_english_history_stays_hebrew(provider_id, gateway):
    org = uuid.uuid4()
    tid = uuid.uuid4()
    seen: dict = {}

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        seen["system"] = system
        seen["message"] = message
        yield ("ok", f"{provider_id}-model", gateway)

    with _stream_patches(tid, fake_stream, history_text=_EN * 4):
        async for _ in stream_chat_response(
            _HE, "u", str(org), "free", thread_id=tid, provider_id=provider_id
        ):
            pass

    assert "Respond in Hebrew" in seen["system"]
    assert "Respond in English" not in seen["system"]
    assert _EN in seen["message"]
    assert _HE in seen["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id,gateway", _PROVIDERS)
async def test_stream_hebrew_instruction_with_english_large_paste(provider_id, gateway):
    org = uuid.uuid4()
    tid = uuid.uuid4()
    paste = "SOURCE DOCUMENT " + ("lorem ipsum dolor sit amet " * 400)
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "תסכם את זה"},
            {
                "type": "large_paste",
                "id": "lp1",
                "label": "Pasted text",
                "text": paste,
                "char_count": len(paste),
            },
        ]
    )
    seen: dict = {}

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        seen["system"] = system
        seen["message"] = message
        yield ("ok", f"{provider_id}-model", gateway)

    with _stream_patches(tid, fake_stream):
        async for _ in stream_chat_response(
            encoded, "u", str(org), "free", thread_id=tid, provider_id=provider_id
        ):
            pass

    assert "Respond in Hebrew" in seen["system"]
    assert "Respond in English" not in seen["system"]
    assert paste in seen["message"]
    assert "Language preference" not in seen["message"]


@pytest.mark.asyncio
async def test_provider_switch_reuses_same_language_system():
    org = uuid.uuid4()
    tid = uuid.uuid4()
    systems: dict[str, str] = {}

    for provider_id, gateway in _PROVIDERS:
        async def fake_stream(
            message, tenant_id, tier, *, provider_id=None, model_override=None, system=None, _gw=gateway
        ):
            systems[provider_id] = system
            yield ("ok", "m", _gw)

        with _stream_patches(tid, fake_stream):
            async for _ in stream_chat_response(
                _HE, "u", str(org), "free", thread_id=tid, provider_id=provider_id
            ):
                pass

    assert set(systems) == {p for p, _ in _PROVIDERS}
    assert len({systems[p] for p, _ in _PROVIDERS}) == 1
    assert "Respond in Hebrew" in next(iter(systems.values()))


@pytest.mark.asyncio
async def test_handle_chat_does_not_wrap_user_payload(monkeypatch):
    captured: dict = {}

    async def fake_route(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["message"] = message
        captured["system"] = system
        return {"content": "ok", "model_used": "m", "provider_used": "openai", "cost_usd": 0.0}

    class _Session:
        def add_all(self, rows):
            captured["persisted"] = rows[0].content

        async def execute(self, *a, **k):
            return None

        async def commit(self):
            return None

        async def flush(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("services.chat_service.route_request", fake_route)
    monkeypatch.setattr("services.chat_service.resolve_thread_id", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr("services.chat_service.get_db_session", lambda: _Session())
    monkeypatch.setattr(
        "services.chat_service.build_chat_message_with_thread_context",
        AsyncMock(side_effect=lambda _o, _t, m: m),
    )
    monkeypatch.setattr("services.chat_service.persist_chat_exchange_sqlite", lambda *a, **k: (1, 2))
    monkeypatch.setattr("services.chat_service.capture_chat_exchange", AsyncMock())

    await handle_chat(_HE, "u", str(uuid.uuid4()), "free", provider_id="claude")
    assert captured["message"] == _HE
    assert "Respond in Hebrew" in captured["system"]
