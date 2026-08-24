"""Gate 1 follow-up — persist Used files / unavailable_count on the chat envelope."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.chat_service as chat_service
from services.message_format import decode_message, encode_chat_assistant
from services.workspace_files.service import WorkspaceFilesContext

READY_ID = "2b595b7e-88e5-4c45-9841-c639450520bb"
READY_NAME = "phase4b_scheduler_canary_20260816.txt"
QUEUED_ID = "0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4"
ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_encode_stores_used_files_and_unavailable_count():
    raw = encode_chat_assistant(
        "answer",
        model_used="gpt-4o-mini",
        cost_usd=0.01,
        provider_id="gpt",
        used_files=[{"id": READY_ID, "name": READY_NAME}],
        unavailable_count=1,
    )
    payload = json.loads(raw)
    assert payload["kind"] == "chat"
    assert payload["used_files"] == [{"id": READY_ID, "name": READY_NAME}]
    assert payload["unavailable_count"] == 1
    assert QUEUED_ID not in raw
    assert "queued" not in raw.lower()


def test_decode_restores_used_files_after_refresh():
    raw = encode_chat_assistant(
        "answer",
        model_used="gpt-4o-mini",
        cost_usd=0.01,
        used_files=[{"id": READY_ID, "name": READY_NAME}],
        unavailable_count=1,
    )
    out = decode_message("assistant", raw)
    assert out["content"] == "answer"
    assert out["used_files"] == [{"id": READY_ID, "name": READY_NAME}]
    assert out["unavailable_count"] == 1


def test_old_envelope_without_fields_still_decodes():
    raw = json.dumps(
        {
            "ben": 1,
            "kind": "chat",
            "text": "legacy answer",
            "model_used": "gpt-4o-mini",
            "cost_usd": 0.0,
        }
    )
    out = decode_message("assistant", raw)
    assert out["content"] == "legacy answer"
    assert out["used_files"] == []
    assert out["unavailable_count"] == 0


def test_empty_used_files_stays_empty_and_is_omitted_from_envelope():
    raw = encode_chat_assistant(
        "no files",
        model_used="gpt-4o-mini",
        cost_usd=0.01,
        used_files=[],
        unavailable_count=0,
    )
    payload = json.loads(raw)
    assert "used_files" not in payload
    assert "unavailable_count" not in payload
    out = decode_message("assistant", raw)
    assert out["used_files"] == []
    assert out["unavailable_count"] == 0


def test_name_without_id_is_not_persisted():
    raw = encode_chat_assistant(
        "answer",
        model_used="gpt-4o-mini",
        cost_usd=0.01,
        used_files=[{"name": "inferred-only.txt"}, {"id": READY_ID, "name": READY_NAME}],
        unavailable_count=0,
    )
    payload = json.loads(raw)
    assert payload["used_files"] == [{"id": READY_ID, "name": READY_NAME}]
    assert "inferred-only.txt" not in raw


def test_persistence_does_not_introduce_queued_or_foreign_names():
    raw = encode_chat_assistant(
        "The model mentioned other_org_secret.txt and queued.txt",
        model_used="gpt-4o-mini",
        cost_usd=0.01,
        used_files=[{"id": READY_ID, "name": READY_NAME}],
        unavailable_count=1,
    )
    out = decode_message("assistant", raw)
    names = [item["name"] for item in out["used_files"]]
    ids = [item["id"] for item in out["used_files"]]
    assert names == [READY_NAME]
    assert ids == [READY_ID]
    assert "queued.txt" not in names
    assert "other_org_secret.txt" not in names
    assert QUEUED_ID not in ids


def _patch_stream_pipeline(monkeypatch, captured):
    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["message"] = message
        yield ("ok", "model-x", "openai")

    async def _aid(*a, **k):
        return None

    def capture_sqlite(*a, **k):
        captured["assistant_content"] = k.get("assistant_content") or (a[2] if len(a) > 2 else None)
        return (1, 2)

    monkeypatch.setattr("services.chat_service.resolve_thread_id", lambda *a, **k: _aid())
    monkeypatch.setattr("services.chat_service.is_project_setup_thread", lambda _tid: False)

    async def _ctx(_o, _t, m):
        return m

    async def _knowledge(_m, payload):
        return payload

    monkeypatch.setattr("services.chat_service.build_chat_message_with_thread_context", _ctx)
    monkeypatch.setattr("services.chat_service.inject_knowledge_few_shot", _knowledge)
    monkeypatch.setattr("services.chat_service.apply_language_context", lambda msg, _lang: msg)
    monkeypatch.setattr("services.chat_service.route_request_stream", fake_stream)
    monkeypatch.setattr("services.chat_service.persist_chat_exchange_sqlite", capture_sqlite)
    monkeypatch.setattr("services.chat_service._schedule_chat_persist", lambda *_a, **_k: None)
    monkeypatch.setattr("services.chat_service.run_copilot_preamble", AsyncMock(return_value=[]))


@pytest.mark.asyncio
async def test_live_done_and_stored_envelope_use_same_injected_files(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    used = [{"id": READY_ID, "name": READY_NAME}]

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, **_k):
        return WorkspaceFilesContext(
            block=f'<workspace_files>\n[file name="{READY_NAME}"]\nCANARY\n[/file]\n</workspace_files>',
            count=1,
            chars=6,
            truncated=False,
            used_files=tuple(used),
            unavailable_count=1,
        )

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)
    events = []
    async for line in chat_service.stream_chat_response(
        "What is in phase4b_scheduler_canary_20260816.txt?",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(json.loads(line))
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_used"] == used
    assert done["workspace_files_unavailable_count"] == 1
    stored = decode_message("assistant", captured["assistant_content"])
    assert stored["used_files"] == used
    assert stored["unavailable_count"] == 1
    assert QUEUED_ID not in json.dumps(stored)
