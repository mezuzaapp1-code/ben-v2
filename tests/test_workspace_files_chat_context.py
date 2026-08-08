"""Workspace Files -> Chat Context bridge.

Covers the read-only context helper (isolation, status/empty filtering, size cap)
and the injection point in stream_chat_response (block reaches route_request_stream,
identical context for every provider, failure never breaks chat, diagnostics).
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


def _row(*, org_id=ORG_A, workspace_id=WS_A, status="ready", text="content", name="doc.pdf", created_at=0, rid=None):
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
    """Async-context session stub; returns the same rows for every execute()."""

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


# --------------------------------------------------------------------------- #
# load_ready_files_context — isolation, filtering, cap                        #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ready_file_text_is_included(monkeypatch):
    _patch_session(monkeypatch, [_row(text="Hello from PDF", name="TLV062.PDF")])
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.count == 1
    assert out.chars == len("Hello from PDF")
    assert out.truncated is False
    assert out.block.startswith("<workspace_files>")
    assert '[file name="TLV062.PDF"]' in out.block
    assert "Hello from PDF" in out.block
    assert out.block.endswith("</workspace_files>")


@pytest.mark.asyncio
async def test_non_ready_files_are_ignored(monkeypatch):
    rows = [
        _row(status="processing", text="not ready yet"),
        _row(status="failed", text="failed extract"),
        _row(status="uploaded", text="just uploaded"),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.count == 0
    assert out.block == ""


@pytest.mark.asyncio
async def test_other_workspace_is_excluded(monkeypatch):
    rows = [_row(workspace_id=WS_B, text="belongs to another workspace")]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.count == 0
    assert "another workspace" not in out.block


@pytest.mark.asyncio
async def test_other_org_is_excluded(monkeypatch):
    rows = [_row(org_id=ORG_B, text="belongs to another org")]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.count == 0
    assert "another org" not in out.block


@pytest.mark.asyncio
async def test_empty_extracted_text_is_ignored(monkeypatch):
    rows = [_row(text=""), _row(text="   "), _row(text=None)]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.count == 0
    assert out.block == ""


@pytest.mark.asyncio
async def test_max_chars_cap_truncates_deterministically(monkeypatch):
    rows = [
        _row(text="A" * 100, name="one.txt", created_at=1),
        _row(text="B" * 100, name="two.txt", created_at=2),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=150)
    assert out.truncated is True
    assert out.chars == 150  # 100 from file one + 50 from file two
    assert out.count == 2
    assert ("A" * 100) in out.block
    assert ("B" * 50) in out.block
    assert ("B" * 51) not in out.block


@pytest.mark.asyncio
async def test_max_chars_zero_returns_empty(monkeypatch):
    _patch_session(monkeypatch, [_row(text="anything")])
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=0)
    assert out.count == 0 and out.block == "" and out.truncated is False


@pytest.mark.asyncio
async def test_multiple_files_preserve_order(monkeypatch):
    rows = [
        _row(text="first", name="a.txt", created_at=1),
        _row(text="second", name="b.txt", created_at=2),
    ]
    _patch_session(monkeypatch, rows)
    out = await load_ready_files_context(ORG_A, WS_A, max_chars=10_000)
    assert out.count == 2
    assert out.block.index('a.txt') < out.block.index('b.txt')


# --------------------------------------------------------------------------- #
# stream_chat_response — injection, provider parity, failure safety           #
# --------------------------------------------------------------------------- #

def _patch_stream_pipeline(monkeypatch, captured):
    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["message"] = message
        captured.setdefault("messages", []).append(message)
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


async def _run_stream(provider_id, project_id=WS_A, message="Explain the uploaded file"):
    events = []
    async for line in chat_service.stream_chat_response(
        message,
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id=provider_id,
        project_id=project_id,
    ):
        events.append(json.loads(line))
    return events


_BLOCK = '<workspace_files>\n[file name="TLV062.PDF"]\nSECRET-ANSWER-42\n[/file]\n</workspace_files>'


@pytest.mark.asyncio
async def test_ready_file_reaches_route_request_stream(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def fake_ctx(_org, _ws, *, max_chars):
        return WorkspaceFilesContext(block=_BLOCK, count=1, chars=16, truncated=False)

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)

    events = await _run_stream("gpt")
    assert "SECRET-ANSWER-42" in captured["message"]
    assert captured["message"].startswith("<workspace_files>")
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_injected"] is True
    assert done["workspace_files_count"] == 1
    assert done["workspace_files_chars"] == 16


@pytest.mark.asyncio
async def test_gpt_and_claude_receive_identical_context(monkeypatch):
    async def fake_ctx(_org, _ws, *, max_chars):
        return WorkspaceFilesContext(block=_BLOCK, count=1, chars=16, truncated=False)

    monkeypatch.setattr("services.chat_service.load_ready_files_context", fake_ctx)

    cap_gpt: dict = {}
    _patch_stream_pipeline(monkeypatch, cap_gpt)
    await _run_stream("gpt")

    cap_claude: dict = {}
    _patch_stream_pipeline(monkeypatch, cap_claude)
    await _run_stream("claude")

    assert "SECRET-ANSWER-42" in cap_gpt["message"]
    assert "SECRET-ANSWER-42" in cap_claude["message"]
    # The workspace file context block is identical regardless of provider.
    assert cap_gpt["message"].split("</workspace_files>")[0] == (
        cap_claude["message"].split("</workspace_files>")[0]
    )


@pytest.mark.asyncio
async def test_no_workspace_means_no_injection(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def boom(*a, **k):  # must not be called when project_id is None
        raise AssertionError("load_ready_files_context should not be called without a workspace")

    monkeypatch.setattr("services.chat_service.load_ready_files_context", boom)
    events = await _run_stream("gpt", project_id=None)
    assert "<workspace_files>" not in captured["message"]
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_injected"] is False


@pytest.mark.asyncio
async def test_file_context_failure_does_not_break_chat(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)

    async def boom(_org, _ws, *, max_chars):
        raise RuntimeError("db exploded")

    monkeypatch.setattr("services.chat_service.load_ready_files_context", boom)
    events = await _run_stream("gpt")

    # Chat still completes, no file context, and no false claim of availability.
    assert "<workspace_files>" not in captured["message"]
    done = next(e for e in events if e["type"] == "done")
    assert done["type"] == "done"
    assert done["workspace_files_injected"] is False
    assert done["workspace_files_count"] == 0
