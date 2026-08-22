"""Large Paste V1 — conversation-scoped user_turn parts."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from database.models import Message
from services.message_format import (
    LARGE_PASTE_THRESHOLD,
    LARGE_PASTE_UNWRAP_CEILING,
    PROVIDER_EXPANDED_MAX_CHARS,
    decode_message,
    encode_chat_assistant,
    encode_user_turn,
    expand_user_message_for_provider,
    format_large_paste_stub,
    provider_expansion_too_large,
    thread_title_from_user_message,
    user_turn_focus_query_source,
)
from services.rolling_context import build_rolling_context_prompt
from services.thread_service import format_full_thread_history_for_handoff

REPO = Path(__file__).resolve().parents[1]


def _paste_part(text: str, *, pid: str = "paste-1", label: str = "Pasted text") -> dict:
    return {
        "type": "large_paste",
        "id": pid,
        "label": label,
        "text": text,
        "char_count": len(text),
    }


def test_threshold_and_unwrap_constants():
    assert LARGE_PASTE_THRESHOLD == 10_000
    assert LARGE_PASTE_UNWRAP_CEILING == 25_000
    assert PROVIDER_EXPANDED_MAX_CHARS == 400_000


def test_legacy_user_and_assistant_decode_unchanged():
    assert decode_message("user", "plain hello") == {"role": "user", "content": "plain hello"}
    raw = encode_chat_assistant("ok", model_used="gpt-4o-mini", cost_usd=0.01, provider_id="gpt")
    out = decode_message("assistant", raw)
    assert out["content"] == "ok"
    assert out["provider_id"] == "gpt"
    assert "parts" not in out


def test_user_typed_json_that_is_not_user_turn_stays_raw():
    raw = json.dumps({"ben": 1, "kind": "chat", "text": "nope"})
    assert decode_message("user", raw) == {"role": "user", "content": raw}
    invalid = '{"ben":1,"kind":"user_turn","parts":[{"type":"nope"}]}'
    assert decode_message("user", invalid)["content"] == invalid


def test_text_only_encode_is_legacy_string():
    encoded = encode_user_turn([{"type": "text", "text": "Review this."}])
    assert encoded == "Review this."
    assert not encoded.startswith('{"ben":')


def test_ordered_parts_round_trip_and_expand():
    paste = "P" * 12_000
    parts = [
        {"type": "text", "text": "Before.\n"},
        _paste_part(paste),
        {"type": "text", "text": "\nAfter."},
    ]
    encoded = encode_user_turn(parts)
    payload = json.loads(encoded)
    assert payload == {
        "ben": 1,
        "kind": "user_turn",
        "parts": [
            {"type": "text", "text": "Before.\n"},
            _paste_part(paste),
            {"type": "text", "text": "\nAfter."},
        ],
    }
    decoded = decode_message("user", encoded)
    assert decoded["kind"] == "user_turn"
    assert decoded["parts"][0]["text"] == "Before.\n"
    assert decoded["parts"][1]["text"] == paste
    assert decoded["parts"][2]["text"] == "\nAfter."
    assert paste not in decoded["content"]
    assert format_large_paste_stub(12_000) in decoded["content"]
    assert '{"ben":' not in decoded["content"]
    assert expand_user_message_for_provider(encoded) == f"Before.\n{paste}\nAfter."


def test_multiple_pastes_preserve_order():
    p1 = "ONE-" + ("א" * 10_000)
    p2 = "TWO-" + ("ב" * 10_000)
    parts = [
        _paste_part(p1, pid="a"),
        {"type": "text", "text": " mid "},
        _paste_part(p2, pid="b", label="Pasted text 2"),
        {"type": "text", "text": " end"},
    ]
    encoded = encode_user_turn(parts)
    assert expand_user_message_for_provider(encoded) == f"{p1} mid {p2} end"
    decoded = decode_message("user", encoded)
    assert [part["id"] for part in decoded["parts"] if part["type"] == "large_paste"] == ["a", "b"]


def test_paste_only_expand_and_focus_stub():
    paste = "Q" * 80_000
    encoded = encode_user_turn([_paste_part(paste)])
    assert expand_user_message_for_provider(encoded) == paste
    assert user_turn_focus_query_source(encoded) == format_large_paste_stub(80_000)
    assert paste not in user_turn_focus_query_source(encoded)


def test_focus_uses_instruction_never_full_paste():
    paste = "X" * 20_000
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "What is the opening width?"},
            _paste_part(paste),
        ]
    )
    assert user_turn_focus_query_source(encoded) == "What is the opening width?"
    assert paste not in user_turn_focus_query_source(encoded)


def test_hebrew_emoji_markdown_exact():
    body = "שלום 🌍\n```js\nconst x = 1;\n```\n" + ("מדריך " * 2000)
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "בדוק:\n"},
            _paste_part(body, pid="he"),
            {"type": "text", "text": "\nתודה"},
        ]
    )
    assert expand_user_message_for_provider(encoded) == f"בדוק:\n{body}\nתודה"
    decoded = decode_message("user", encoded)
    assert decoded["parts"][1]["text"] == body
    assert decoded["parts"][1]["char_count"] == len(body)


def test_200k_persists_exactly():
    body = "נ" * 200_000
    encoded = encode_user_turn([{"type": "text", "text": "Note:\n"}, _paste_part(body, pid="big")])
    assert len(expand_user_message_for_provider(encoded)) == 6 + 200_000
    assert decode_message("user", encoded)["parts"][1]["text"] == body
    assert provider_expansion_too_large(expand_user_message_for_provider(encoded)) is None


def test_1mb_class_is_explicit_not_truncated():
    body = "M" * 1_000_000
    encoded = encode_user_turn([_paste_part(body, pid="huge")])
    expanded = expand_user_message_for_provider(encoded)
    assert expanded == body
    err = provider_expansion_too_large(expanded)
    assert err is not None
    assert "1,000,000" in err
    assert "not truncated" in err.lower()


def test_history_and_rolling_context_use_stub_not_body():
    paste = "H" * 80_124
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "Review this architecture. "},
            _paste_part(paste),
            {"type": "text", "text": " Focus on retrieval."},
        ]
    )
    org = uuid.uuid4()
    tid = uuid.uuid4()
    rows = [
        Message(role="user", content=encoded, org_id=org, thread_id=tid),
        Message(
            role="assistant",
            content=encode_chat_assistant("Noted.", model_used="m", cost_usd=0.0, provider_id="gpt"),
            org_id=org,
            thread_id=tid,
        ),
    ]
    history = format_full_thread_history_for_handoff(rows)
    assert history is not None
    assert paste not in history
    assert '{"ben":' not in history
    assert format_large_paste_stub(80_124) in history
    assert "Review this architecture." in history
    assert "Focus on retrieval." in history
    rolling = build_rolling_context_prompt(rows, opinion_request="Add opinion")
    assert paste not in rolling
    assert format_large_paste_stub(80_124) in rolling


def test_thread_title_uses_display_not_json():
    encoded = encode_user_turn([{"type": "text", "text": "Hello "}, _paste_part("Z" * 12_000)])
    title = thread_title_from_user_message(encoded)
    assert not title.startswith("{")
    assert "Hello" in title
    assert "Large paste" in title


def test_large_paste_does_not_touch_workspace_file_modules():
    message_format = (REPO / "services/message_format.py").read_text(encoding="utf-8")
    chat_service = (REPO / "services/chat_service.py").read_text(encoding="utf-8")
    large_paste_js = (REPO / "frontend/src/lib/largePaste.js").read_text(encoding="utf-8")
    composer = (REPO / "frontend/src/components/ComposerCapsule.jsx").read_text(encoding="utf-8")
    assert "from services.workspace_files" not in message_format
    assert "import workspace_files" not in message_format
    assert "handleWorkspaceFileAttach" not in composer
    assert "workspaces/" not in large_paste_js
    assert "user_turn_focus_query_source(message)" in chat_service
    assert "expand_user_message_for_provider(message)" in chat_service
    assert "load_ready_files_context" in chat_service


def test_thread_sqlite_owns_large_paste_lifecycle(tmp_path, monkeypatch):
    from database import thread_store
    from services.thread_service import persist_chat_exchange_sqlite

    monkeypatch.setattr(thread_store, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(thread_store, "_SYSTEM_DB", tmp_path / "system_main.db")
    monkeypatch.setattr(thread_store, "_THREADS_DIR", tmp_path / "threads")
    thread_store.init_thread_store()

    tid = uuid.uuid4()
    paste = "L" * 18_000
    encoded = encode_user_turn([{"type": "text", "text": "Keep "}, _paste_part(paste, pid="life")])
    persist_chat_exchange_sqlite(
        tid,
        user_text=encoded,
        assistant_content=encode_chat_assistant("ack", model_used="m", cost_usd=0.0, provider_id="gpt"),
        provider="gpt",
    )
    rows = thread_store.list_thread_messages(str(tid))
    user_row = next(row for row in rows if row.role == "user")
    decoded = decode_message("user", user_row.content)
    assert decoded["parts"][1]["text"] == paste
    assert user_row.content.startswith('{"ben":')
    db_path = thread_store.resolve_thread_db_path(str(tid))
    assert db_path.exists()
    assert thread_store.delete_thread_database_file(str(tid)) is True
    assert not db_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id,gateway", [("gpt", "openai"), ("claude", "anthropic"), ("gemini", "google")])
async def test_current_turn_providers_receive_full_expansion(provider_id, gateway):
    from services.chat_service import stream_chat_response

    paste = "BODY-" + ("ק" * 20_000)
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "Review:\n"},
            _paste_part(paste, pid="prov"),
            {"type": "text", "text": "\nThanks"},
        ]
    )
    org = uuid.uuid4()
    tid = uuid.uuid4()
    seen: dict = {}
    persisted: dict = {}

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        seen["message"] = message
        seen["provider_id"] = provider_id
        yield ("ok", f"{provider_id}-model", gateway)

    def fake_sqlite(thread_id, *, user_text, assistant_content, provider=None):
        persisted["user_text"] = user_text
        persisted["provider"] = provider
        return (11, 12)

    workspace_queries: list[str] = []

    async def fake_ws(_org, _ws, *, max_chars, user_query=None, **_k):
        workspace_queries.append(user_query or "")
        from services.workspace_files.service import WorkspaceFilesContext

        return WorkspaceFilesContext(block="", count=0, chars=0, truncated=False)

    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
        patch("services.chat_service.is_project_setup_thread", return_value=False),
        patch(
            "services.chat_service.build_chat_message_with_thread_context",
            new=AsyncMock(side_effect=lambda _o, _t, m: m),
        ),
        patch("services.chat_service.inject_knowledge_few_shot", new=AsyncMock(side_effect=lambda _m, p: p)),
        patch("services.chat_service.apply_language_context", side_effect=lambda msg, _lang: msg),
        patch("services.chat_service.route_request_stream", side_effect=fake_stream),
        patch("services.chat_service.persist_chat_exchange_sqlite", side_effect=fake_sqlite),
        patch("services.chat_service._schedule_chat_persist"),
        patch("services.chat_service.load_ready_files_context", side_effect=fake_ws),
        patch("services.chat_service.run_copilot_preamble", new=AsyncMock(return_value=[])),
    ):
        events = []
        async for line in stream_chat_response(
            encoded,
            "user-1",
            str(org),
            "free",
            thread_id=tid,
            provider_id=provider_id,
            project_id=uuid.uuid4(),
        ):
            events.append(json.loads(line))

    assert seen["provider_id"] == provider_id
    assert seen["message"] == f"Review:\n{paste}\nThanks"
    assert '{"ben":' not in seen["message"]
    assert persisted["user_text"] == encoded
    assert workspace_queries == ["Review:\n\nThanks"]
    assert paste not in workspace_queries[0]
    assert any(e["type"] == "done" for e in events)


@pytest.mark.asyncio
async def test_oversize_current_turn_is_honest_and_not_persisted():
    from services.chat_service import stream_chat_response

    encoded = encode_user_turn([_paste_part("Z" * 500_000, pid="too-big")])
    routed = {"called": False}

    async def fake_stream(*_a, **_k):
        routed["called"] = True
        yield ("nope", "m", "openai")

    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=uuid.uuid4())),
        patch("services.chat_service.is_project_setup_thread", return_value=False),
        patch("services.chat_service.route_request_stream", side_effect=fake_stream),
        patch("services.chat_service.persist_chat_exchange_sqlite") as persist,
        patch("services.chat_service._schedule_chat_persist") as schedule,
    ):
        events = []
        async for line in stream_chat_response(
            encoded,
            "user-1",
            str(uuid.uuid4()),
            "free",
            provider_id="gpt",
        ):
            events.append(json.loads(line))

    assert routed["called"] is False
    persist.assert_not_called()
    schedule.assert_not_called()
    err = next(e for e in events if e["type"] == "error")
    assert "not truncated" in err["message"].lower()
    assert "500,000" in err["message"]
