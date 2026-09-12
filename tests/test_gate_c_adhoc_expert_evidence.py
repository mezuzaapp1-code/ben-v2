"""Gate C — Add Opinion UI path uses the canonical Workspace File assembler."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from services.global_service_store import init_global_service_schema
from services.message_format import decode_message, encode_adhoc_expert
from services.rolling_context import DEFAULT_OPINION_REQUEST
from services.workspace_files.multi_source import MODE_CLARIFY, SourceResolution
from services.workspace_files.service import WorkspaceFilesContext
from tests.helpers_auth import patch_main_persistent_tenant
from database.thread_store import (
    init_thread_store,
    insert_thread_message,
    list_thread_messages,
    upsert_thread_metadata,
)

ORG = "00000000-0000-0000-0000-000000000001"
FILE_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1")
CHUNK_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_QUESTION = "What is the termination clause in A.pdf?"

_BLOCK = (
    '<workspace_files>\n[file name="A.pdf"]\nSECRET-CLAUSE-99\n[/file]\n</workspace_files>'
)
_USED = [{"id": str(FILE_A), "name": "A.pdf"}]
_EVIDENCE = {
    "retrieval_mode": "chunks",
    "sources": [
        {
            "source_id": str(FILE_A),
            "source_type": "workspace_file",
            "display_name": "A.pdf",
        }
    ],
    "evidence": [
        {
            "evidence_id": f"chunk:{CHUNK_A}",
            "source_id": str(FILE_A),
            "excerpt": "termination for convenience",
            "origin": "ben_retrieval",
            "chunk_id": str(CHUNK_A),
            "page": 2,
        }
    ],
}


@pytest.fixture(autouse=True)
def _gate_c_customer():
    with patch_main_persistent_tenant(ORG):
        yield


@pytest.fixture()
def opinion_env(tmp_path, monkeypatch):
    system_db = tmp_path / "system_main.db"
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    monkeypatch.setenv("BEN_THREADS_DATA_DIR", str(threads_dir))
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    init_global_service_schema()
    init_thread_store()
    return {"system_db": system_db, "threads_dir": threads_dir}


def _seed_gpt_thread(user_text: str = USER_QUESTION) -> tuple[str, int]:
    tid = str(uuid.uuid4())
    upsert_thread_metadata(thread_id=tid, org_id=ORG, title="Gate C GPT")
    insert_thread_message(tid, role="user", content=user_text)
    gpt_id = insert_thread_message(
        tid,
        role="assistant",
        content="GPT draft answer.",
        provider="gpt",
        message_type="chat",
    )
    return tid, int(gpt_id)


def _ctx() -> WorkspaceFilesContext:
    return WorkspaceFilesContext(
        block=_BLOCK,
        count=1,
        chars=len(_BLOCK),
        truncated=False,
        used_files=tuple(_USED),
        response_evidence=_EVIDENCE,
    )


async def _collect(stream) -> list[dict]:
    events = []
    async for line in stream:
        events.append(json.loads(line))
    return events


def _patch_source_passthrough(monkeypatch):
    monkeypatch.setattr(
        "services.expert_opinion_service.load_source_state",
        AsyncMock(return_value={}),
    )


@pytest.mark.asyncio
async def test_anchored_add_opinion_uses_canonical_assembler(opinion_env, monkeypatch):
    from services.expert_opinion_service import stream_expert_opinion

    tid, anchor_id = _seed_gpt_thread()
    captured: dict = {}
    _patch_source_passthrough(monkeypatch)

    async def fake_ctx(org, project_id, *, max_chars, user_query=None, **_k):
        captured["org"] = org
        captured["project_id"] = project_id
        captured["user_query"] = user_query
        captured["max_chars"] = max_chars
        captured["called"] = captured.get("called", 0) + 1
        return _ctx()

    async def fake_route(prompt, tenant_id, tier, **kwargs):
        captured["prompt"] = prompt
        captured["provider_id"] = kwargs.get("provider_id")
        yield ("Claude reads the clause.", "claude-test", "anthropic")

    monkeypatch.setattr("services.expert_opinion_service.load_ready_files_context", fake_ctx)
    monkeypatch.setattr("services.expert_opinion_service.route_request_stream", fake_route)

    events = await _collect(
        stream_expert_opinion(
            uuid.UUID(ORG),
            uuid.UUID(tid),
            session_id=uuid.uuid4(),
            provider_id="claude",
            tenant_id=ORG,
            tier="free",
            anchor_message_id=anchor_id,
            project_id=WS_A,
        )
    )

    assert captured.get("called") == 1
    assert captured["user_query"] == USER_QUESTION
    assert captured["user_query"] != DEFAULT_OPINION_REQUEST
    assert captured["project_id"] == WS_A
    assert captured["prompt"].startswith("<workspace_files>")
    assert "SECRET-CLAUSE-99" in captured["prompt"]
    assert USER_QUESTION in captured["prompt"]
    assert captured["provider_id"] == "claude"

    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_used"] == _USED
    assert done["response_evidence"]["sources"][0]["source_id"] == str(FILE_A)
    assert done["kind"] == "adhoc_expert"

    rows = list_thread_messages(tid)
    joined = "\n".join(str(r.content or "") for r in rows)
    assert "<workspace_files>" not in joined
    assert "SECRET-CLAUSE-99" not in joined

    adhoc_rows = [r for r in rows if r.provider == "claude"]
    assert len(adhoc_rows) == 1
    stored = decode_message("assistant", adhoc_rows[0].content)
    assert stored["kind"] == "adhoc_expert"
    assert stored["used_files"] == _USED
    assert stored["response_evidence"]["sources"][0]["source_id"] == str(FILE_A)
    assert "<workspace_files>" not in stored["content"]
    assert "<workspace_files>" not in json.dumps(json.loads(adhoc_rows[0].content))


@pytest.mark.asyncio
async def test_panel_opinion_mode_uses_same_assembler(opinion_env, monkeypatch):
    from services.expert_opinion_service import stream_expert_opinion

    tid, anchor_id = _seed_gpt_thread()
    captured: dict = {}
    _patch_source_passthrough(monkeypatch)

    async def fake_ctx(*_a, **kwargs):
        captured["user_query"] = kwargs.get("user_query")
        return _ctx()

    async def fake_route(prompt, *_a, **_k):
        captured["prompt"] = prompt
        yield ("Panel view.", "claude-test", "anthropic")

    monkeypatch.setattr("services.expert_opinion_service.load_ready_files_context", fake_ctx)
    monkeypatch.setattr("services.expert_opinion_service.route_request_stream", fake_route)

    events = await _collect(
        stream_expert_opinion(
            uuid.UUID(ORG),
            uuid.UUID(tid),
            session_id=uuid.uuid4(),
            provider_id="claude",
            tenant_id=ORG,
            tier="free",
            anchor_message_id=anchor_id,
            message_type="panel",
            project_id=WS_A,
        )
    )
    done = next(e for e in events if e["type"] == "done")
    assert captured["user_query"] == USER_QUESTION
    assert captured["prompt"].startswith("<workspace_files>")
    assert done["message_type"] == "panel"
    assert done["workspace_files_used"] == _USED


@pytest.mark.asyncio
async def test_clarify_fail_closed_skips_file_block(opinion_env, monkeypatch):
    from services.expert_opinion_service import stream_expert_opinion

    tid, anchor_id = _seed_gpt_thread()
    captured: dict = {}
    monkeypatch.setattr(
        "services.expert_opinion_service.load_source_state",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "services.expert_opinion_service.resolve_turn_sources",
        lambda *_a, **_k: SourceResolution(mode=MODE_CLARIFY, file_ids=(), reason="test"),
    )

    async def boom(*_a, **_k):
        raise AssertionError("clarify must not call load_ready_files_context")

    async def fake_route(prompt, *_a, **_k):
        captured["prompt"] = prompt
        yield ("Independent opinion from transcript.", "claude-test", "anthropic")

    monkeypatch.setattr("services.expert_opinion_service.load_ready_files_context", boom)
    monkeypatch.setattr("services.expert_opinion_service.route_request_stream", fake_route)

    events = await _collect(
        stream_expert_opinion(
            uuid.UUID(ORG),
            uuid.UUID(tid),
            session_id=uuid.uuid4(),
            provider_id="claude",
            tenant_id=ORG,
            tier="free",
            anchor_message_id=anchor_id,
            project_id=WS_A,
        )
    )
    done = next(e for e in events if e["type"] == "done")
    assert "<workspace_files>" not in captured["prompt"]
    assert done.get("workspace_files_used") == []
    assert "response_evidence" not in done
    adhoc = [r for r in list_thread_messages(tid) if r.provider == "claude"][0]
    stored = decode_message("assistant", adhoc.content)
    assert "used_files" not in stored
    assert "response_evidence" not in stored
    assert "Independent opinion from transcript." in done["response"]


@pytest.mark.asyncio
async def test_no_project_id_skips_assembler(opinion_env, monkeypatch):
    from services.expert_opinion_service import stream_expert_opinion

    tid, anchor_id = _seed_gpt_thread()

    async def boom(*_a, **_k):
        raise AssertionError("load_ready_files_context should not run without project_id")

    async def fake_route(prompt, *_a, **_k):
        yield ("No files.", "claude-test", "anthropic")

    monkeypatch.setattr("services.expert_opinion_service.load_ready_files_context", boom)
    monkeypatch.setattr("services.expert_opinion_service.route_request_stream", fake_route)

    events = await _collect(
        stream_expert_opinion(
            uuid.UUID(ORG),
            uuid.UUID(tid),
            session_id=uuid.uuid4(),
            provider_id="claude",
            tenant_id=ORG,
            tier="free",
            anchor_message_id=anchor_id,
        )
    )
    done = next(e for e in events if e["type"] == "done")
    assert done["response"] == "No files."
    assert done.get("workspace_files_used") == []
    assert "response_evidence" not in done


@pytest.mark.asyncio
async def test_assembler_error_is_fail_soft(opinion_env, monkeypatch):
    from services.expert_opinion_service import stream_expert_opinion

    tid, anchor_id = _seed_gpt_thread()
    _patch_source_passthrough(monkeypatch)

    async def boom(*_a, **_k):
        raise RuntimeError("assembler down")

    async def fake_route(prompt, *_a, **_k):
        assert "<workspace_files>" not in prompt
        yield ("Still streamed.", "claude-test", "anthropic")

    monkeypatch.setattr("services.expert_opinion_service.load_ready_files_context", boom)
    monkeypatch.setattr("services.expert_opinion_service.route_request_stream", fake_route)

    events = await _collect(
        stream_expert_opinion(
            uuid.UUID(ORG),
            uuid.UUID(tid),
            session_id=uuid.uuid4(),
            provider_id="claude",
            tenant_id=ORG,
            tier="free",
            anchor_message_id=anchor_id,
            project_id=WS_A,
        )
    )
    done = next(e for e in events if e["type"] == "done")
    assert done["response"] == "Still streamed."
    assert done.get("workspace_files_used") == []
    assert "response_evidence" not in done


def test_encode_adhoc_expert_evidence_roundtrip():
    raw = encode_adhoc_expert(
        session_id=str(uuid.uuid4()),
        provider_id="claude",
        response="Independent take.",
        provider_used="anthropic",
        model="claude-test",
        used_files=_USED,
        response_evidence=_EVIDENCE,
    )
    payload = json.loads(raw)
    assert payload["kind"] == "adhoc_expert"
    assert payload["used_files"] == _USED
    assert payload["response_evidence"]["sources"][0]["source_id"] == str(FILE_A)
    decoded = decode_message("assistant", raw)
    assert decoded["kind"] == "adhoc_expert"
    assert decoded["used_files"] == _USED
    assert decoded["response_evidence"]["sources"][0]["display_name"] == "A.pdf"
    assert "Independent take." in decoded["content"]


def test_old_adhoc_envelope_without_evidence_hydrates():
    raw = encode_adhoc_expert(
        session_id=str(uuid.uuid4()),
        provider_id="claude",
        response="Legacy opinion.",
        model="claude-old",
    )
    payload = json.loads(raw)
    assert "used_files" not in payload
    assert "response_evidence" not in payload
    decoded = decode_message("assistant", raw)
    assert decoded["kind"] == "adhoc_expert"
    assert "used_files" not in decoded
    assert "response_evidence" not in decoded
    assert "Legacy opinion." in decoded["content"]


def test_adhoc_malformed_evidence_is_dropped():
    raw = encode_adhoc_expert(
        session_id=str(uuid.uuid4()),
        provider_id="claude",
        response="x",
        used_files=_USED,
        response_evidence={"retrieval_mode": "nope", "sources": [], "evidence": []},
    )
    payload = json.loads(raw)
    assert payload["used_files"] == _USED
    assert "response_evidence" not in payload
    decoded = decode_message("assistant", raw)
    assert decoded["used_files"] == _USED
    assert "response_evidence" not in decoded


def test_api_invalid_project_id_is_422(opinion_env):
    import main
    from fastapi.testclient import TestClient

    tid, anchor_id = _seed_gpt_thread()
    client = TestClient(main.app)
    response = client.post(
        f"/api/threads/{tid}/adhoc/expert/stream",
        json={
            "session_id": str(uuid.uuid4()),
            "provider_id": "claude",
            "tier": "free",
            "anchor_message_id": anchor_id,
            "project_id": "not-a-uuid",
        },
    )
    assert response.status_code == 422
    assert "Invalid project_id" in response.text
