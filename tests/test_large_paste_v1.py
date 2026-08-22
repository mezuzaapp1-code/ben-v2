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
    user_turn_copilot_intent_source,
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
    assert "in one request" in err
    assert "transport limit" in err
    assert "to the model" not in err
    assert "guarantee" in err


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


def test_user_turn_survives_sqlite_persist_reload_expand_and_history_stub(tmp_path, monkeypatch):
    """Storage-boundary proof: persist envelope, reload via production decode, expand, stub."""
    from database import thread_store
    from services.message_format import expand_user_message_for_provider, format_large_paste_stub
    from services.thread_service import (
        _sqlite_messages_for_api,
        format_full_thread_history_for_handoff,
        persist_chat_exchange_sqlite,
        thread_store_messages_as_chat_rows,
    )

    monkeypatch.setattr(thread_store, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(thread_store, "_SYSTEM_DB", tmp_path / "system_main.db")
    monkeypatch.setattr(thread_store, "_THREADS_DIR", tmp_path / "threads")
    thread_store.init_thread_store()

    tid = uuid.uuid4()
    canary = "CANARY-END-שלום-🌍-```md"
    paste = ("א" * 12_000) + "\n" + canary
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "Before.\n"},
            _paste_part(paste, pid="store-1"),
            {"type": "text", "text": "\nAfter."},
        ]
    )
    persist_chat_exchange_sqlite(
        tid,
        user_text=encoded,
        assistant_content=encode_chat_assistant("ack", model_used="m", cost_usd=0.0, provider_id="gpt"),
        provider="gpt",
    )

    stored = thread_store.list_thread_messages(str(tid))
    user_row = next(row for row in stored if row.role == "user")
    assert user_row.content == encoded
    stored_payload = json.loads(user_row.content)
    assert stored_payload["kind"] == "user_turn"
    assert stored_payload["parts"][1]["text"] == paste
    assert stored_payload["parts"][1]["text"].endswith(canary)

    api_messages = _sqlite_messages_for_api(tid)
    reloaded = next(m for m in api_messages if m["role"] == "user")
    assert reloaded["kind"] == "user_turn"
    assert reloaded["parts"][1]["text"] == paste
    assert reloaded["parts"][1]["text"].endswith(canary)
    assert reloaded["parts"][0]["text"] == "Before.\n"
    assert reloaded["parts"][2]["text"] == "\nAfter."
    assert paste not in reloaded["content"]
    assert '{"ben":' not in reloaded["content"]
    assert format_large_paste_stub(len(paste)) in reloaded["content"]

    expanded = expand_user_message_for_provider(user_row.content)
    assert expanded == f"Before.\n{paste}\nAfter."
    assert expanded.endswith("After.")
    assert canary in expanded
    assert '{"ben":' not in expanded

    history = format_full_thread_history_for_handoff(thread_store_messages_as_chat_rows(stored))
    assert history is not None
    assert paste not in history
    assert canary not in history
    assert '{"ben":' not in history
    assert format_large_paste_stub(len(paste)) in history
    assert "Before." in history
    assert "After." in history


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
    assert "transport limit" in err["message"]
    assert "to the model" not in err["message"]


PRODUCTION_CANARY = "BEN-LP-END-92741"


def _production_failure_paste() -> str:
    body = (
        "Large Paste production verification document.\n"
        "Travel to verify BEN after reading the full body.\n"
        "This also mentions government intelligence and site intelligence.\n"
    )
    body += "X" * 12_000
    return body + (
        "\nRead the unique verification code below and return ONLY that exact code, "
        f"with no additional words.\n\n{PRODUCTION_CANARY}"
    )


def test_copilot_intent_ignores_large_paste_body():
    paste = _production_failure_paste()
    assert "to verify BEN" in paste
    assert "intelligence" in paste
    encoded = encode_user_turn([_paste_part(paste, pid="intent")])
    intent = user_turn_copilot_intent_source(encoded)
    expanded = expand_user_message_for_provider(encoded)
    assert expanded == paste
    assert PRODUCTION_CANARY in expanded
    assert paste not in intent
    assert "to verify BEN" not in intent
    assert "intelligence" not in intent
    assert format_large_paste_stub(len(paste)) == intent


def test_copilot_intent_keeps_explicit_instruction_and_raw_short_chat():
    paste = _production_failure_paste()
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "@intel check the project\n"},
            _paste_part(paste, pid="cmd"),
        ]
    )
    intent = user_turn_copilot_intent_source(encoded)
    assert intent == "@intel check the project"
    assert paste not in intent
    assert user_turn_copilot_intent_source("@intel check this site") == "@intel check this site"
    assert user_turn_focus_query_source(encoded) == intent


@pytest.mark.asyncio
async def test_large_paste_body_does_not_trigger_copilot_or_memory_writes():
    from services.chat_service import stream_chat_response
    from services.copilot_orchestrator import run_copilot_preamble as real_preamble

    paste = _production_failure_paste()
    encoded = encode_user_turn([_paste_part(paste, pid="prod-fail")])
    org = uuid.uuid4()
    tid = uuid.uuid4()
    project_id = uuid.uuid4()
    seen: dict = {}

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        seen["message"] = message
        yield (PRODUCTION_CANARY, "gpt-test", "openai")

    async def spy_preamble(text, org_id, proj_id):
        seen["copilot_intent"] = text
        return await real_preamble(text, org_id, proj_id)

    save_memory = AsyncMock()
    ambient = AsyncMock(return_value=None)
    intel = AsyncMock()

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
        patch("services.chat_service.persist_chat_exchange_sqlite", return_value=(11, 12)),
        patch("services.chat_service._schedule_chat_persist"),
        patch("services.chat_service.run_copilot_preamble", side_effect=spy_preamble),
        patch("services.copilot_orchestrator.apply_ambient_memory_from_message", new=ambient),
        patch("services.copilot_orchestrator.fetch_site_intelligence", new=intel),
        patch("services.project_copilot_tools.save_project_memory", new=save_memory),
    ):
        events = []
        async for line in stream_chat_response(
            encoded,
            "user-1",
            str(org),
            "free",
            thread_id=tid,
            provider_id="gpt",
            project_id=project_id,
        ):
            events.append(json.loads(line))

    assert seen["message"] == paste
    assert PRODUCTION_CANARY in seen["message"]
    assert '{"ben":' not in seen["message"]
    assert seen["copilot_intent"] != paste
    assert paste not in seen["copilot_intent"]
    assert "to verify BEN" not in seen["copilot_intent"]
    assert "intelligence" not in seen["copilot_intent"]
    assert format_large_paste_stub(len(paste)) == seen["copilot_intent"]
    assert not any(e.get("type") == "mutated_state" for e in events)
    ambient.assert_awaited()
    assert "to verify BEN" not in str(ambient.await_args)
    intel.assert_not_awaited()
    save_memory.assert_not_awaited()
    done = next(e for e in events if e["type"] == "done")
    assert done["response"] == PRODUCTION_CANARY


@pytest.mark.asyncio
async def test_explicit_intel_instruction_outside_paste_still_triggers_copilot():
    from services.chat_service import stream_chat_response
    from services.copilot_orchestrator import run_copilot_preamble as real_preamble

    paste = _production_failure_paste()
    encoded = encode_user_turn(
        [
            {"type": "text", "text": "@intel check the project\n"},
            _paste_part(paste, pid="cmd-intel"),
        ]
    )
    org = uuid.uuid4()
    tid = uuid.uuid4()
    project_id = uuid.uuid4()
    seen: dict = {}
    intel = AsyncMock(
        return_value={
            "mutated_state": {
                "card_type": "government_intelligence",
                "payload": {"tool": "fetch_site_intelligence", "query": "project"},
            }
        }
    )

    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        seen["message"] = message
        yield (PRODUCTION_CANARY, "gpt-test", "openai")

    async def spy_preamble(text, org_id, proj_id):
        seen["copilot_intent"] = text
        return await real_preamble(text, org_id, proj_id)

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
        patch("services.chat_service.persist_chat_exchange_sqlite", return_value=(11, 12)),
        patch("services.chat_service._schedule_chat_persist"),
        patch("services.chat_service.run_copilot_preamble", side_effect=spy_preamble),
        patch("services.copilot_orchestrator.apply_ambient_memory_from_message", new=AsyncMock(return_value=None)),
        patch("services.copilot_orchestrator.fetch_site_intelligence", new=intel),
    ):
        events = []
        async for line in stream_chat_response(
            encoded,
            "user-1",
            str(org),
            "free",
            thread_id=tid,
            provider_id="gpt",
            project_id=project_id,
        ):
            events.append(json.loads(line))

    assert seen["copilot_intent"] == "@intel check the project"
    assert paste not in seen["copilot_intent"]
    assert PRODUCTION_CANARY in seen["message"]
    assert paste in seen["message"]
    intel.assert_awaited()
    cards = [e for e in events if e.get("type") == "mutated_state"]
    assert any(e.get("card_type") == "government_intelligence" for e in cards)
    done = next(e for e in events if e["type"] == "done")
    assert done["response"] == PRODUCTION_CANARY


@pytest.mark.asyncio
async def test_short_chat_still_reaches_copilot_unchanged():
    from services.chat_service import stream_chat_response
    from services.copilot_orchestrator import run_copilot_preamble as real_preamble

    org = uuid.uuid4()
    tid = uuid.uuid4()
    seen: dict = {}
    intel = AsyncMock(
        return_value={
            "mutated_state": {"card_type": "government_intelligence", "payload": {"query": "site"}}
        }
    )

    async def fake_stream(message, *_a, **_k):
        seen["message"] = message
        yield ("ok", "m", "openai")

    async def spy_preamble(text, org_id, proj_id):
        seen["copilot_intent"] = text
        return await real_preamble(text, org_id, proj_id)

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
        patch("services.chat_service.persist_chat_exchange_sqlite", return_value=(1, 2)),
        patch("services.chat_service._schedule_chat_persist"),
        patch("services.chat_service.run_copilot_preamble", side_effect=spy_preamble),
        patch("services.copilot_orchestrator.apply_ambient_memory_from_message", new=AsyncMock(return_value=None)),
        patch("services.copilot_orchestrator.fetch_site_intelligence", new=intel),
    ):
        events = []
        async for line in stream_chat_response(
            "@intel check this site",
            "user-1",
            str(org),
            "free",
            thread_id=tid,
            provider_id="gpt",
            project_id=uuid.uuid4(),
        ):
            events.append(json.loads(line))

    assert seen["copilot_intent"] == "@intel check this site"
    assert seen["message"] == "@intel check this site"
    intel.assert_awaited()
    assert any(e.get("card_type") == "government_intelligence" for e in events)
