"""F1a current-turn Vision: composer image → authorized bytes → native provider input."""
from __future__ import annotations

import inspect
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-anthropic")
os.environ.setdefault("GOOGLE_API_KEY", "gk-test-google")
os.environ.setdefault("XAI_API_KEY", "xai-test-key")

import services.chat_service as chat_service
from services.chat_language import assemble_chat_system
from services.execution_plan import (
    VISION_CAPABILITY_DENIED_MESSAGE,
    resolve_execution_plan,
)
from services.message_format import (
    encode_user_turn,
    expand_user_message_for_provider,
    format_file_ref_stub,
)
from services.providers.anthropic_provider import AnthropicProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.model_registry import model_has_capability
from services.providers.openai_provider import OpenAIProvider
from services.providers.vision_input import (
    VISION_ANALYZE,
    UserTextPart,
    VisionImage,
    anthropic_user_content,
    gemini_user_parts,
    openai_user_content,
)
from services.providers.xai_provider import XAIProvider
from services.vision.current_turn import (
    VisionTurnError,
    build_provider_user_content,
    load_current_turn_vision_images,
    user_turn_file_ref_ids,
)
from services.workspace_files import drain as drain_mod
from services.workspace_resolver import resolve_workspace_context_for_org

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
WS = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WS_OTHER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
FILE_A = uuid.UUID("33333333-3333-4333-8333-333333333333")
FILE_B = uuid.UUID("44444444-4444-4444-8444-444444444444")
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
HEBREW_Q = "מה הצבע של הריבוע בתמונה?"


def _png(file_id=FILE_A) -> VisionImage:
    return VisionImage(file_id=str(file_id), media_type="image/png", data=TINY_PNG)


def _turn(text: str | None = HEBREW_Q, file_id=FILE_A, name="square.png"):
    parts = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.append({"type": "file_ref", "file_id": str(file_id), "name": name})
    return encode_user_turn(parts)


def _image_only(file_id=FILE_A, name="square.png"):
    return encode_user_turn([{"type": "file_ref", "file_id": str(file_id), "name": name}])


def _ordered_turn():
    return encode_user_turn(
        [
            {"type": "text", "text": "Look at this "},
            {"type": "file_ref", "file_id": str(FILE_A), "name": "a.png"},
            {"type": "text", "text": " then this "},
            {"type": "file_ref", "file_id": str(FILE_B), "name": "b.png"},
            {"type": "text", "text": " please."},
        ]
    )


def _leak_haystack(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _assert_no_ben_leaks(payload) -> None:
    blob = _leak_haystack(payload)
    assert "storage_key" not in blob
    assert "/var/" not in blob
    assert "/workspace/" not in blob
    assert '"ben":' not in blob
    assert '{"ben":' not in blob
    assert "extracted_text" not in blob
    assert "WorkspaceFile" not in blob


def _workspace():
    return resolve_workspace_context_for_org(str(ORG), project_id=str(WS))


def _provider_user_content(text=HEBREW_Q, image=None):
    image = image or _png()
    return [UserTextPart(text), image]


# --------------------------------------------------------------------------- #
# Capability matrix
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "provider, model, expected",
    [
        ("openai", "gpt-5.5-instant", True),
        ("openai", "gpt-5.5-pro", True),
        ("openai", "gpt-4o", True),
        ("anthropic", "claude-opus-4.8", True),
        ("anthropic", "claude-sonnet-4.6", True),
        ("google", "gemini-3.5-flash", True),
        ("xai", "grok-4.6", True),
        ("xai", "grok-4.3", True),
        ("openai", "o1", False),
        ("openai", "o1-mini", False),
        ("openai", "o3-mini", False),
    ],
)
def test_vision_capability_matrix(provider, model, expected):
    assert model_has_capability(provider, model, VISION_ANALYZE) is expected


def test_execution_plan_allows_vision_model():
    plan = resolve_execution_plan(
        _workspace(),
        VISION_ANALYZE,
        requested_resource="gpt-5.5-instant",
        selected_model="gpt-5.5-instant",
        gateway_provider="openai",
    )
    assert plan.enforced is True
    assert plan.allowed is True
    assert plan.capability_key == VISION_ANALYZE


def test_execution_plan_denies_non_vision_model():
    plan = resolve_execution_plan(
        _workspace(),
        VISION_ANALYZE,
        requested_resource="o1-mini",
        selected_model="o1-mini",
        gateway_provider="openai",
    )
    assert plan.enforced is True
    assert plan.allowed is False
    assert "vision.analyze" in (plan.deny_reason or "")


# --------------------------------------------------------------------------- #
# Envelope / order / language
# --------------------------------------------------------------------------- #
def test_image_only_turn_encodes_envelope():
    encoded = _image_only()
    payload = json.loads(encoded)
    assert payload["kind"] == "user_turn"
    assert payload["parts"][0]["type"] == "file_ref"
    assert payload["parts"][0]["file_id"] == str(FILE_A)
    assert expand_user_message_for_provider(encoded) == ""
    assert format_file_ref_stub("square.png") == "[Attached image · square.png]"


def test_text_plus_image_order_preserved_in_provider_content():
    images = [_png(FILE_A), _png(FILE_B)]
    parts = build_provider_user_content(_ordered_turn(), images)
    assert [type(p).__name__ for p in parts] == [
        "UserTextPart",
        "VisionImage",
        "UserTextPart",
        "VisionImage",
        "UserTextPart",
    ]
    assert parts[0].text == "Look at this "
    assert parts[1].file_id == str(FILE_A)
    assert parts[2].text == " then this "
    assert parts[3].file_id == str(FILE_B)
    assert parts[4].text == " please."


def test_hebrew_instruction_stays_hebrew_with_image():
    encoded = _turn()
    system = assemble_chat_system(encoded, None)
    assert "Respond in Hebrew" in system
    assert "file excerpts" in system
    assert "visible image text" in system
    user_parts = build_provider_user_content(encoded, [_png()])
    assert any(isinstance(p, UserTextPart) and p.text == HEBREW_Q for p in user_parts)


def test_file_ref_does_not_expand_bytes_as_text():
    encoded = _turn()
    expanded = expand_user_message_for_provider(encoded)
    assert expanded == HEBREW_Q
    assert TINY_PNG not in expanded.encode("utf-8", errors="ignore")
    assert "storage_key" not in encoded


# --------------------------------------------------------------------------- #
# Native provider payloads
# --------------------------------------------------------------------------- #
def test_openai_payload_contains_text_and_image():
    body_messages = OpenAIProvider()._messages(
        HEBREW_Q, "sys", user_content=_provider_user_content()
    )
    user = body_messages[-1]["content"]
    assert isinstance(user, list)
    assert user[0] == {"type": "text", "text": HEBREW_Q}
    assert user[1]["type"] == "image_url"
    assert user[1]["image_url"]["url"].startswith("data:image/png;base64,")
    _assert_no_ben_leaks(body_messages)


def test_anthropic_payload_contains_text_and_image():
    body = AnthropicProvider()._body(
        "claude-sonnet-4.6", HEBREW_Q, "sys", stream=True, user_content=_provider_user_content()
    )
    content = body["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": HEBREW_Q}
    assert content[1]["type"] == "image"
    assert content[1]["source"]["type"] == "base64"
    assert content[1]["source"]["media_type"] == "image/png"
    assert content[1]["source"]["data"]
    _assert_no_ben_leaks(body)


def test_gemini_payload_contains_text_and_image():
    payload = GeminiProvider()._payload(HEBREW_Q, "sys", user_content=_provider_user_content())
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"text": HEBREW_Q}
    assert parts[1]["inlineData"]["mimeType"] == "image/png"
    assert parts[1]["inlineData"]["data"]
    _assert_no_ben_leaks(payload)


def test_xai_payload_contains_text_and_image_and_no_search():
    body = XAIProvider()._json_body(
        "grok-4.6", HEBREW_Q, "sys", stream=True, user_content=_provider_user_content()
    )
    user = body["messages"][-1]["content"]
    assert isinstance(user, list)
    assert user[0]["text"] == HEBREW_Q
    assert user[1]["type"] == "image_url"
    assert "search_parameters" not in body
    assert "tools" not in body
    _assert_no_ben_leaks(body)


def test_text_only_provider_payloads_unchanged():
    openai_msgs = OpenAIProvider()._messages("hello", None)
    assert openai_msgs[-1]["content"] == "hello"
    anthropic = AnthropicProvider()._body("claude-sonnet-4.6", "hello", None, stream=False)
    assert anthropic["messages"][0]["content"] == "hello"
    gemini = GeminiProvider()._payload("hello", None)
    assert gemini["contents"][0]["parts"] == [{"text": "hello"}]
    xai = XAIProvider()._json_body("grok-4.6", "hello", None, stream=True)
    assert xai["messages"][-1]["content"] == "hello"
    assert set(xai) == {"model", "messages", "stream", "stream_options"}


# --------------------------------------------------------------------------- #
# Authz
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unauthorized_file_id_rejected(monkeypatch):
    async def _missing(**_k):
        raise HTTPException(status_code=404, detail="File not found")

    monkeypatch.setattr("services.vision.current_turn.open_file_bytes", _missing)
    with pytest.raises(VisionTurnError, match="not available in the current workspace"):
        await load_current_turn_vision_images(
            org_id=ORG, workspace_id=WS, file_ids=[str(FILE_A)]
        )


@pytest.mark.asyncio
async def test_cross_workspace_file_rejected(monkeypatch):
    async def _missing(**_k):
        raise HTTPException(status_code=404, detail="File not found")

    monkeypatch.setattr("services.vision.current_turn.open_file_bytes", _missing)
    with pytest.raises(VisionTurnError, match="not available"):
        await load_current_turn_vision_images(
            org_id=ORG, workspace_id=WS_OTHER, file_ids=[str(FILE_A)]
        )


@pytest.mark.asyncio
async def test_missing_bytes_fail_honestly(monkeypatch):
    async def _gone(**_k):
        raise HTTPException(status_code=404, detail="File bytes missing")

    monkeypatch.setattr("services.vision.current_turn.open_file_bytes", _gone)
    with pytest.raises(VisionTurnError, match="Image bytes are missing"):
        await load_current_turn_vision_images(
            org_id=ORG, workspace_id=WS, file_ids=[str(FILE_A)]
        )


@pytest.mark.asyncio
async def test_loader_does_not_require_ready_or_extraction(monkeypatch, tmp_path):
    path = tmp_path / "square.png"
    path.write_bytes(TINY_PNG)

    async def _open(*, org_id, workspace_id, file_id):
        assert org_id == ORG
        assert workspace_id == WS
        assert file_id == FILE_A
        return path, "image/png", "square.png"

    monkeypatch.setattr("services.vision.current_turn.open_file_bytes", _open)
    images = await load_current_turn_vision_images(
        org_id=ORG, workspace_id=WS, file_ids=[str(FILE_A)]
    )
    assert images[0].data == TINY_PNG
    source = inspect.getsource(load_current_turn_vision_images)
    assert "extracted_text" not in source
    assert "index_status" not in source
    assert "load_ready_files_context" not in source
    assert "chunk_retrieval" not in source


def test_vision_does_not_drive_document_drain():
    source = inspect.getsource(load_current_turn_vision_images)
    assert "drain" not in source
    assert "extract_text" not in source
    chat_src = inspect.getsource(chat_service._current_turn_vision_user_content)
    assert "drain_document_processing" not in chat_src
    assert drain_mod.drain_document_processing_jobs.__name__ == "drain_document_processing_jobs"


# --------------------------------------------------------------------------- #
# Chat stream integration
# --------------------------------------------------------------------------- #
def _patch_stream_pipeline(monkeypatch, captured):
    async def fake_stream(
        message,
        tenant_id,
        tier,
        *,
        provider_id=None,
        model_override=None,
        system=None,
        user_content=None,
    ):
        captured["message"] = message
        captured["system"] = system
        captured["user_content"] = user_content
        captured["provider_id"] = provider_id
        captured["model_override"] = model_override
        captured["dispatched"] = True
        yield ("ok", model_override or "gpt-5.5-instant", "openai")

    async def _aid(*a, **k):
        return uuid.uuid4()

    monkeypatch.setattr("services.chat_service.resolve_thread_id", lambda *a, **k: _aid())
    monkeypatch.setattr("services.chat_service.is_project_setup_thread", lambda _tid: False)

    async def _ctx(_o, _t, m):
        return m

    async def _knowledge(_m, payload):
        return payload

    async def _copilot(*_a, **_k):
        return []

    monkeypatch.setattr("services.chat_service.build_chat_message_with_thread_context", _ctx)
    monkeypatch.setattr("services.chat_service.inject_knowledge_few_shot", _knowledge)
    monkeypatch.setattr("services.chat_service.route_request_stream", fake_stream)
    monkeypatch.setattr("services.chat_service.run_copilot_preamble", _copilot)
    monkeypatch.setattr("services.chat_service.persist_chat_exchange_sqlite", lambda *a, **k: (1, 2))
    monkeypatch.setattr("services.chat_service._schedule_chat_persist", lambda *_a, **_k: None)


async def _collect(message, monkeypatch, captured, **kwargs):
    events = []
    async for line in chat_service.stream_chat_response(
        message,
        "user-1",
        str(ORG),
        "free",
        thread_id=uuid.uuid4(),
        project_id=WS,
        **kwargs,
    ):
        events.append(json.loads(line))
    return events


@pytest.mark.asyncio
async def test_stream_sends_user_content_without_waiting_for_3d(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def _load(**_k):
        return [_png()]

    monkeypatch.setattr("services.chat_service.load_current_turn_vision_images", _load)

    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    retrieval_calls = {"n": 0}

    async def _ready_fail(*_a, **_k):
        retrieval_calls["n"] += 1
        raise AssertionError("current-turn Vision must not call Gate 3D/4A retrieval")

    monkeypatch.setattr("services.chat_service.load_ready_files_context", _ready_fail)

    events = await _collect(_turn(), monkeypatch, captured, provider_id="gpt")
    assert retrieval_calls["n"] == 0
    assert captured["dispatched"] is True
    assert captured["user_content"] is not None
    assert any(isinstance(p, VisionImage) for p in captured["user_content"])
    assert any(isinstance(p, UserTextPart) and p.text == HEBREW_Q for p in captured["user_content"])
    assert '{"ben":' not in captured["message"]
    assert next(e for e in events if e["type"] == "chunk")["content"] == "ok"


@pytest.mark.asyncio
async def test_image_only_stream_is_valid(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    monkeypatch.setattr(
        "services.chat_service.load_current_turn_vision_images",
        AsyncMock(return_value=[_png()]),
    )
    events = await _collect(_image_only(), monkeypatch, captured, provider_id="gpt")
    assert captured["user_content"] is not None
    assert all(
        isinstance(p, VisionImage) or (isinstance(p, UserTextPart) and p.text)
        for p in captured["user_content"]
    )
    assert any(e["type"] == "chunk" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["gpt", "claude", "gemini", "grok"])
async def test_vision_routes_to_selected_provider(monkeypatch, provider_id):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    monkeypatch.setattr(
        "services.chat_service.load_current_turn_vision_images",
        AsyncMock(return_value=[_png()]),
    )
    await _collect(_turn(), monkeypatch, captured, provider_id=provider_id)
    assert captured["provider_id"] == provider_id
    assert captured["user_content"]


@pytest.mark.asyncio
async def test_non_vision_model_denied_before_dispatch(monkeypatch):
    captured: dict = {"dispatched": False}
    _patch_stream_pipeline(monkeypatch, captured)

    async def _must_not_load(**_k):
        raise AssertionError("capability deny must happen before reading bytes")

    monkeypatch.setattr("services.chat_service.load_current_turn_vision_images", _must_not_load)
    events = await _collect(
        _turn(),
        monkeypatch,
        captured,
        provider_id="gpt",
        model_override="o1-mini",
    )
    err = next(e for e in events if e["type"] == "error")
    assert "vision.analyze" in err["message"]
    assert captured.get("dispatched") is not True
    assert captured.get("user_content") is None


@pytest.mark.asyncio
async def test_failed_provider_does_not_require_text_fallback(monkeypatch):
    captured: dict = {}

    async def boom(*_a, **_k):
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    persist = {"called": False}

    def _persist(*_a, **_k):
        persist["called"] = True
        return (1, 2)

    async def _aid(*a, **k):
        return uuid.uuid4()

    monkeypatch.setattr("services.chat_service.resolve_thread_id", lambda *a, **k: _aid())
    monkeypatch.setattr("services.chat_service.is_project_setup_thread", lambda _tid: False)
    monkeypatch.setattr(
        "services.chat_service.build_chat_message_with_thread_context",
        AsyncMock(side_effect=lambda _o, _t, m: m),
    )
    monkeypatch.setattr(
        "services.chat_service.inject_knowledge_few_shot",
        AsyncMock(side_effect=lambda _m, p: p),
    )
    monkeypatch.setattr("services.chat_service.run_copilot_preamble", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.chat_service.route_request_stream", boom)
    monkeypatch.setattr("services.chat_service.persist_chat_exchange_sqlite", _persist)
    monkeypatch.setattr("services.chat_service._schedule_chat_persist", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "services.chat_service.load_current_turn_vision_images",
        AsyncMock(return_value=[_png()]),
    )
    events = []
    async for line in chat_service.stream_chat_response(
        _turn(),
        "user-1",
        str(ORG),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=WS,
    ):
        events.append(json.loads(line))
    assert any(e["type"] == "error" for e in events)
    assert persist["called"] is False


@pytest.mark.asyncio
async def test_text_chat_does_not_send_user_content(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    events = await _collect("Just text, no image.", monkeypatch, captured, provider_id="gpt")
    assert captured.get("user_content") is None
    assert any(e["type"] == "chunk" for e in events)


@pytest.mark.asyncio
async def test_large_paste_still_expands_without_images(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    paste = "P" * 12_000
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "Summarize:\n"},
            {
                "type": "large_paste",
                "id": "p1",
                "label": "Pasted text",
                "text": paste,
                "char_count": 12_000,
            },
        ]
    )
    await _collect(encoded, monkeypatch, captured, provider_id="gpt")
    assert captured.get("user_content") is None
    assert paste in captured["message"]


def test_native_helpers_omit_internal_envelope():
    parts = _provider_user_content()
    _assert_no_ben_leaks(openai_user_content(parts))
    _assert_no_ben_leaks(anthropic_user_content(parts))
    _assert_no_ben_leaks(gemini_user_parts(parts))


@pytest.mark.asyncio
async def test_workspace_required_before_byte_load():
    with pytest.raises(VisionTurnError, match="active workspace"):
        await load_current_turn_vision_images(
            org_id=ORG, workspace_id=None, file_ids=[str(FILE_A)]
        )


@pytest.mark.asyncio
async def test_non_image_media_type_rejected(monkeypatch, tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("not an image", encoding="utf-8")

    async def _open(**_k):
        return path, "text/plain", "note.txt"

    monkeypatch.setattr("services.vision.current_turn.open_file_bytes", _open)
    with pytest.raises(VisionTurnError, match="PNG, JPEG, GIF, or WEBP"):
        await load_current_turn_vision_images(
            org_id=ORG, workspace_id=WS, file_ids=[str(FILE_A)]
        )


def test_user_turn_file_ref_ids_ignore_client_storage_key():
    encoded = encode_user_turn(
        [
            {
                "type": "file_ref",
                "file_id": str(FILE_A),
                "name": "square.png",
                "storage_key": "/secret/path.png",
            }
        ]
    )
    payload = json.loads(encoded)
    assert "storage_key" not in json.dumps(payload["parts"])
    assert user_turn_file_ref_ids(encoded) == [str(FILE_A)]
