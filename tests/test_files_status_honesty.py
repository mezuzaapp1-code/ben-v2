"""Gate 1 — file status honesty + used-files diagnostics.

Proves READY-only injection, used_files from the same tenant/workspace
retrieval path, and no fabricated or cross-tenant filenames.
"""
from __future__ import annotations

import json
import sys
import types
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.chat_service as chat_service
from services.workspace_files.service import (
    WorkspaceFilesContext,
    load_ready_files_context,
)

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WS_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
READY_ID = uuid.UUID("2b595b7e-88e5-4c45-9841-c639450520bb")
QUEUED_ID = uuid.UUID("0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4")


def _row(
    *,
    org_id=ORG_A,
    workspace_id=WS_A,
    status="ready",
    text="content",
    name="doc.pdf",
    created_at=0,
    rid=None,
):
    return types.SimpleNamespace(
        org_id=org_id,
        workspace_id=workspace_id,
        status=status,
        extracted_text=text,
        display_name=name,
        original_filename=name,
        created_at=created_at,
        id=rid or uuid.uuid4(),
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _FakeResult(self._rows)


def _patch_session(monkeypatch, rows):
    monkeypatch.setattr(
        "services.workspace_files.service.get_db_session",
        lambda: _FakeSession(rows),
    )


def _patch_stream_pipeline(monkeypatch, captured):
    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["message"] = message
        yield ("ok", "model-x", "openai")

    async def _aid(*a, **k):
        return None

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
    monkeypatch.setattr("services.chat_service.persist_chat_exchange_sqlite", lambda *a, **k: (1, 2))
    monkeypatch.setattr("services.chat_service._schedule_chat_persist", lambda *_a, **_k: None)


async def _run_stream(project_id=WS_A, message="Explain the uploaded file"):
    events = []
    async for line in chat_service.stream_chat_response(
        message,
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=project_id,
    ):
        events.append(json.loads(line))
    return events


@pytest.mark.asyncio
async def test_queued_file_is_not_reported_as_used(monkeypatch):
    _patch_session(
        monkeypatch,
        [_row(status="queued", text="queued body", name="queued.txt", rid=QUEUED_ID)],
    )
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.block == ""
    assert out.used_files == ()
    assert out.count == 0
    assert out.unavailable_count == 1
    assert "queued.txt" not in json.dumps(out.used_files)


@pytest.mark.asyncio
async def test_ready_injected_file_is_reported_as_used(monkeypatch):
    _patch_session(
        monkeypatch,
        [
            _row(
                text="canary body",
                name="phase4b_scheduler_canary_20260816.txt",
                rid=READY_ID,
            )
        ],
    )
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.count == 1
    assert "canary body" in out.block
    assert out.used_files == (
        {"id": str(READY_ID), "name": "phase4b_scheduler_canary_20260816.txt"},
    )
    assert out.unavailable_count == 0


@pytest.mark.asyncio
async def test_zero_injected_files_produces_no_fabricated_used_list(monkeypatch):
    _patch_session(monkeypatch, [_row(text=""), _row(text="   ")])
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.block == ""
    assert out.used_files == ()
    assert out.count == 0


@pytest.mark.asyncio
async def test_used_files_never_include_foreign_workspace_or_org_names(monkeypatch):
    local_ready = _row(
        text="local ready body",
        name="phase4b_scheduler_canary_20260816.txt",
        rid=READY_ID,
    )
    rows = [
        _row(
            workspace_id=WS_B,
            text="foreign workspace body",
            name="other_workspace_secret.txt",
        ),
        _row(org_id=ORG_B, text="foreign org body", name="other_org_secret.txt"),
        _row(status="queued", text="queued body", name="queued_local.txt", rid=QUEUED_ID),
        local_ready,
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    names = [item["name"] for item in out.used_files]
    ids = [item["id"] for item in out.used_files]
    assert names == ["phase4b_scheduler_canary_20260816.txt"]
    assert ids == [str(READY_ID)]
    assert "other_workspace_secret.txt" not in names
    assert "other_org_secret.txt" not in names
    assert "queued_local.txt" not in names
    assert "foreign workspace body" not in out.block
    assert "foreign org body" not in out.block
    assert out.unavailable_count == 1


@pytest.mark.asyncio
async def test_gate3d_used_files_match_injected_only(monkeypatch):
    """Gate 3D ranking is unchanged: named file is injected; unused files stay off Used."""
    unused = _row(text="payroll numbers", name="budget.xlsx.txt", created_at=50)
    named = _row(text="CANARY-BODY", name="ben_canary.txt", created_at=1, rid=READY_ID)
    _patch_session(monkeypatch, [unused, named])
    out = await load_ready_files_context(
        ORG_A,
        WS_A,
        max_chars=11,
        per_file_max=11,
        user_query="Read ben_canary.txt",
    )
    assert out.block.count("[file name=") == 1
    assert "CANARY-BODY" in out.block
    assert "payroll" not in out.block
    assert out.used_files == ({"id": str(READY_ID), "name": "ben_canary.txt"},)


@pytest.mark.asyncio
async def test_done_payload_used_files_from_injection_only(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    used = ({"id": str(READY_ID), "name": "phase4b_scheduler_canary_20260816.txt"},)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, **_k):
        return WorkspaceFilesContext(
            block='<workspace_files>\n[file name="phase4b_scheduler_canary_20260816.txt"]\nCANARY\n[/file]\n</workspace_files>',
            count=1,
            chars=6,
            truncated=False,
            used_files=used,
            unavailable_count=1,
        )

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)
    events = await _run_stream()
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_injected"] is True
    assert done["workspace_files_used"] == list(used)
    assert done["workspace_files_unavailable_count"] == 1
    assert "CANARY" in captured["message"]


@pytest.mark.asyncio
async def test_done_payload_does_not_fabricate_used_files_when_not_injected(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def fake_ctx(_org, _ws, *, max_chars, user_query=None, **_k):
        return WorkspaceFilesContext(
            block="",
            count=0,
            chars=0,
            truncated=False,
            used_files=(),
            unavailable_count=2,
        )

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)
    events = await _run_stream()
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_injected"] is False
    assert done["workspace_files_used"] == []
    assert done["workspace_files_unavailable_count"] == 2
    assert "<workspace_files>" not in captured["message"]
