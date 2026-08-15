"""Request-level preferred_language for /chat."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.chat_language import (  # noqa: E402
    apply_language_context,
    build_language_instruction,
    detect_language_code,
    extract_in_message_language_override,
    normalize_language_code,
    resolve_response_language,
)
from services.ops.idempotency import reset_idempotency_registry_for_tests  # noqa: E402
from services.ops.load_governance import reset_load_governor_for_tests  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", TENANT)
    reset_idempotency_registry_for_tests()
    reset_load_governor_for_tests()
    yield


@pytest.fixture(autouse=True)
def _gate_a_customer():
    from tests.helpers_auth import patch_main_persistent_tenant

    with patch_main_persistent_tenant(TENANT):
        yield


@pytest.fixture
def client():
    return TestClient(main.app)


def test_normalize_language_code_valid():
    assert normalize_language_code("he") == "he"
    assert normalize_language_code(" EN ") == "en"
    assert normalize_language_code(None) is None
    assert normalize_language_code("") is None


def test_normalize_language_code_invalid():
    with pytest.raises(ValueError, match="en, he"):
        normalize_language_code("fr")


def test_build_language_instruction_hebrew():
    text = build_language_instruction("he")
    assert "Respond in Hebrew (he)" in text
    assert "Do not translate code blocks" in text


def test_apply_language_context_prepends_instruction():
    wrapped = apply_language_context("What is 2+2?", "he")
    assert wrapped.endswith("What is 2+2?")
    assert "Language preference: Respond in Hebrew" in wrapped
    assert "What is 2+2?" in wrapped.split("\n\n", 1)[1]


def test_apply_language_context_omitted_unchanged_when_unclear():
    mixed = "abcdabcd" + "אבגדהוזחט"
    assert apply_language_context(mixed, None) == mixed


_HEBREW_PARAGRAPH = (
    "זהו הסבר מפורט על הנושא בשפה העברית לצורכי בדיקה של מערכת הזיהוי האוטומטי."
)
_ENGLISH_PARAGRAPH = (
    "This is an English paragraph that explains the topic in enough detail for language detection."
)


def test_hebrew_paragraph_auto_injects_instruction():
    wrapped = apply_language_context(_HEBREW_PARAGRAPH, None)
    assert "Respond in Hebrew" in wrapped
    assert wrapped.endswith(_HEBREW_PARAGRAPH)


def test_english_paragraph_auto_injects_instruction():
    wrapped = apply_language_context(_ENGLISH_PARAGRAPH, None)
    assert "Respond in English" in wrapped
    assert wrapped.endswith(_ENGLISH_PARAGRAPH)


def test_mixed_fifty_fifty_no_instruction():
    mixed = "abcdabcd" + "אבגדהוזחט"
    assert apply_language_context(mixed, None) == mixed
    assert detect_language_code(mixed) is None


def test_code_only_no_instruction():
    msg = "```python\nprint('hello')\n```"
    assert apply_language_context(msg, None) == msg


def test_hebrew_prose_with_code_block_hebrew():
    msg = f"{_HEBREW_PARAGRAPH}\n\n```python\nx = 1\n```"
    wrapped = apply_language_context(msg, None)
    assert "Respond in Hebrew" in wrapped


def test_english_prose_with_code_block_english():
    msg = f"{_ENGLISH_PARAGRAPH}\n\n```js\nconst x = 1;\n```"
    wrapped = apply_language_context(msg, None)
    assert "Respond in English" in wrapped


def test_short_hebrew_word_no_instruction():
    assert apply_language_context("שלום", None) == "שלום"


def test_explicit_english_override_in_hebrew_message():
    msg = f"{_HEBREW_PARAGRAPH} answer in English"
    wrapped = apply_language_context(msg, None)
    assert "Respond in English" in wrapped


def test_explicit_hebrew_override_in_english_message():
    msg = f"{_ENGLISH_PARAGRAPH} בעברית"
    wrapped = apply_language_context(msg, None)
    assert "Respond in Hebrew" in wrapped


def test_preferred_language_he_overrides_english_detection():
    wrapped = apply_language_context(_ENGLISH_PARAGRAPH, "he")
    assert "Respond in Hebrew" in wrapped


def test_preferred_language_en_overrides_hebrew_detection():
    wrapped = apply_language_context(_HEBREW_PARAGRAPH, "en")
    assert "Respond in English" in wrapped


def test_extract_in_message_override_phrases():
    assert extract_in_message_language_override("please respond in English") == "en"
    assert extract_in_message_language_override("תענה בעברית על זה") == "he"


def test_chat_invalid_preferred_language_400(client):
    with patch.object(main, "handle_chat", new_callable=AsyncMock):
        r = client.post(
            "/chat",
            json={"message": "hi", "tier": "free", "preferred_language": "fr"},
        )
    assert r.status_code == 400
    assert "preferred_language" in (r.json().get("detail") or "").lower()


def test_chat_passes_preferred_language_to_handler(client):
    captured: dict = {}

    async def fake_chat(
        message, user_id, tenant_id, tier, *,
        thread_id=None,
        provider_id=None,
        model_override=None,
        preferred_language=None,
    ):
        captured["message"] = message
        captured["preferred_language"] = preferred_language
        return {
            "thread_id": str(uuid.uuid4()),
            "response": "ok",
            "model_used": "m",
            "cost_usd": 0.0,
            "provider_id": "gpt",
            "provider_used": "openai",
        }

    with patch.object(main, "handle_chat", side_effect=fake_chat):
        r = client.post(
            "/chat",
            json={"message": "hi", "tier": "free", "preferred_language": "he"},
        )
    assert r.status_code == 200
    assert captured["preferred_language"] == "he"
    assert captured["message"] == "hi"


@pytest.mark.asyncio
async def test_handle_chat_wraps_gateway_persists_raw(monkeypatch):
    from services.chat_service import handle_chat

    captured: dict = {}

    async def fake_route(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["gateway_message"] = message
        return {
            "content": "ok",
            "model_used": "gpt-4o-mini",
            "provider_used": "openai",
            "cost_usd": 0.0,
        }

    async def fake_resolve(org, thread_id, *, title):
        return uuid.uuid4()

    class _Session:
        def add_all(self, rows):
            captured["user_content"] = rows[0].content

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
    monkeypatch.setattr("services.chat_service.resolve_thread_id", fake_resolve)
    monkeypatch.setattr("services.chat_service.get_db_session", lambda: _Session())

    async def passthrough_context(org, tid, msg):
        return msg

    monkeypatch.setattr(
        "services.chat_service.build_chat_message_with_thread_context",
        passthrough_context,
    )

    user_text = "Explain `const x = 1` briefly."
    await handle_chat(user_text, "u", TENANT, "free", provider_id="gpt", preferred_language="he")

    assert captured["user_content"] == user_text
    assert "Language preference: Respond in Hebrew" in captured["gateway_message"]
    assert user_text in captured["gateway_message"]


@pytest.mark.asyncio
async def test_handle_chat_auto_hebrew_persists_raw(monkeypatch):
    from services.chat_service import handle_chat

    captured: dict = {}

    async def fake_route(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["gateway_message"] = message
        return {
            "content": "ok",
            "model_used": "gpt-4o-mini",
            "provider_used": "openai",
            "cost_usd": 0.0,
        }

    async def fake_resolve(org, thread_id, *, title):
        return uuid.uuid4()

    class _Session:
        def add_all(self, rows):
            captured["user_content"] = rows[0].content

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
    monkeypatch.setattr("services.chat_service.resolve_thread_id", fake_resolve)
    monkeypatch.setattr("services.chat_service.get_db_session", lambda: _Session())

    async def passthrough_context(org, tid, msg):
        return msg

    monkeypatch.setattr(
        "services.chat_service.build_chat_message_with_thread_context",
        passthrough_context,
    )

    await handle_chat(_HEBREW_PARAGRAPH, "u", TENANT, "free", provider_id="gpt")

    assert captured["user_content"] == _HEBREW_PARAGRAPH
    assert "Language preference" not in captured["user_content"]
    assert "Respond in Hebrew" in captured["gateway_message"]
    assert _HEBREW_PARAGRAPH in captured["gateway_message"]


@pytest.mark.asyncio
async def test_handle_chat_auto_english_gateway_only(monkeypatch):
    from services.chat_service import handle_chat

    captured: dict = {}

    async def fake_route(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["gateway_message"] = message
        return {"content": "ok", "model_used": "m", "provider_used": "openai", "cost_usd": 0.0}

    async def fake_resolve(org, thread_id, *, title):
        return uuid.uuid4()

    class _Session:
        def add_all(self, rows):
            captured["user_content"] = rows[0].content

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
    monkeypatch.setattr("services.chat_service.resolve_thread_id", fake_resolve)
    monkeypatch.setattr("services.chat_service.get_db_session", lambda: _Session())

    async def passthrough_context(org, tid, msg):
        return msg

    monkeypatch.setattr(
        "services.chat_service.build_chat_message_with_thread_context",
        passthrough_context,
    )

    await handle_chat(_ENGLISH_PARAGRAPH, "u", TENANT, "free", provider_id="gpt")

    assert captured["user_content"] == _ENGLISH_PARAGRAPH
    assert "Respond in English" in captured["gateway_message"]


@pytest.mark.asyncio
async def test_handle_chat_unclear_no_gateway_wrap(monkeypatch):
    from services.chat_service import handle_chat

    captured: dict = {}
    unclear = "abcdabcd" + "אבגדהוזחט"

    async def fake_route(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["gateway_message"] = message
        return {"content": "ok", "model_used": "m", "provider_used": "openai", "cost_usd": 0.0}

    async def fake_resolve(org, thread_id, *, title):
        return uuid.uuid4()

    class _Session:
        def add_all(self, rows):
            captured["user_content"] = rows[0].content

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
    monkeypatch.setattr("services.chat_service.resolve_thread_id", fake_resolve)
    monkeypatch.setattr("services.chat_service.get_db_session", lambda: _Session())

    async def passthrough_context(org, tid, msg):
        return msg

    monkeypatch.setattr(
        "services.chat_service.build_chat_message_with_thread_context",
        passthrough_context,
    )

    await handle_chat(unclear, "u", TENANT, "free", provider_id="gpt")

    assert captured["gateway_message"] == unclear
    assert resolve_response_language(unclear, None) is None


def test_council_body_rejects_preferred_language(client):
    with patch.object(main, "run_council", new_callable=AsyncMock) as mock_council:
        mock_council.return_value = {
            "question": "q",
            "council": [],
            "synthesis": None,
            "cost_usd": 0.0,
            "room": {"id": "x", "question_id": "y", "status": "complete", "member_count": 0},
        }
        r = client.post("/council", json={"question": "q?", "preferred_language": "he"})
    assert r.status_code == 422
