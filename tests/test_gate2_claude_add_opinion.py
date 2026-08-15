"""Gate 2 — Claude Add Opinion without Switchboard dependency."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import main
import pytest
from fastapi.testclient import TestClient
from services.global_service_store import init_global_service_schema
from tests.helpers_auth import patch_main_persistent_tenant
from database.thread_store import (
    init_thread_store,
    insert_thread_message,
    list_thread_messages,
    upsert_thread_metadata,
)

ORG = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _gate_a_customer():
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


def _seed_gpt_thread() -> tuple[str, int]:
    tid = str(uuid.uuid4())
    upsert_thread_metadata(thread_id=tid, org_id=ORG, title="Gate2 GPT")
    insert_thread_message(tid, role="user", content="What is 2+2?")
    gpt_id = insert_thread_message(
        tid,
        role="assistant",
        content="Four.",
        provider="gpt",
        message_type="chat",
    )
    return tid, int(gpt_id)


def test_claude_opinion_succeeds_without_switchboard(opinion_env, monkeypatch):
    reads = {"n": 0}

    def _boom(*_a, **_k):
        reads["n"] += 1
        raise RuntimeError("switchboard must not be read on Add Opinion")

    monkeypatch.setattr("services.global_service_store.list_active_global_channels", _boom)
    monkeypatch.setattr("services.engine_capability_gate.list_active_global_channels", _boom)
    monkeypatch.setattr(
        "services.engine_capability_gate.is_provider_engine_active",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("gate must not run")),
    )

    tid, anchor_id = _seed_gpt_thread()
    client = TestClient(main.app)

    async def _fake_stream(*_a, **_k):
        yield '{"type":"meta","provider_id":"claude","anchor_message_id":%d}\n' % anchor_id
        yield '{"type":"chunk","content":"Claude says four."}\n'
        yield (
            '{"type":"done","thread_id":"%s","response":"Claude says four.",'
            '"provider_id":"claude","model_used":"claude-test",'
            '"sqlite_message_id":99,"anchor_message_id":%d,"kind":"adhoc_expert"}\n'
        ) % (tid, anchor_id)

    with patch.object(main, "stream_expert_opinion", new=_fake_stream):
        response = client.post(
            f"/api/threads/{tid}/adhoc/expert/stream",
            json={
                "session_id": str(uuid.uuid4()),
                "provider_id": "claude",
                "tier": "free",
                "anchor_message_id": anchor_id,
                "opinion_mode": "single",
            },
        )

    assert response.status_code == 200
    assert "not active in workspace" not in response.text
    assert "Claude says four." in response.text
    assert reads["n"] == 0


def test_claude_opinion_nonstream_no_switchboard(opinion_env, monkeypatch):
    monkeypatch.setattr(
        "services.global_service_store.list_active_global_channels",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no switchboard")),
    )
    tid, anchor_id = _seed_gpt_thread()
    client = TestClient(main.app)

    async def _fake_run(*_a, **_k):
        return {
            "thread_id": tid,
            "response": "opinion-ok",
            "provider_id": "claude",
            "model_used": "m",
            "anchor_message_id": anchor_id,
            "kind": "adhoc_expert",
        }

    with patch.object(main, "run_expert_opinion", new=AsyncMock(side_effect=_fake_run)):
        response = client.post(
            f"/api/threads/{tid}/adhoc/expert",
            json={
                "session_id": str(uuid.uuid4()),
                "provider_id": "claude",
                "tier": "free",
                "anchor_message_id": anchor_id,
            },
        )

    assert response.status_code == 200
    assert response.json().get("response") == "opinion-ok"
    assert response.json().get("anchor_message_id") == anchor_id


def test_claude_opinion_persists_on_same_thread(opinion_env, monkeypatch):
    tid, anchor_id = _seed_gpt_thread()

    async def _fake_route(prompt, tenant_id, tier, **kwargs):
        assert kwargs.get("provider_id") == "claude"
        assert "2+2" in prompt or "Four" in prompt
        yield ("Claude agrees: 4.", "claude-test", "anthropic")

    monkeypatch.setattr(
        "services.expert_opinion_service.route_request_stream",
        _fake_route,
    )

    client = TestClient(main.app)
    response = client.post(
        f"/api/threads/{tid}/adhoc/expert/stream",
        json={
            "session_id": str(uuid.uuid4()),
            "provider_id": "claude",
            "tier": "free",
            "anchor_message_id": anchor_id,
        },
    )
    assert response.status_code == 200
    assert "Claude agrees: 4." in response.text

    rows = list_thread_messages(tid)
    assert len(rows) >= 3
    gpt_rows = [r for r in rows if r.provider == "gpt"]
    claude_rows = [r for r in rows if r.provider == "claude"]
    assert gpt_rows, "GPT response must remain"
    assert claude_rows, "Claude opinion must be persisted"
    assert any("Four" in str(r.content or "") for r in gpt_rows)
    assert any(r.insert_after_id == anchor_id for r in claude_rows)


def test_missing_anthropic_key_returns_provider_error(opinion_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    tid, anchor_id = _seed_gpt_thread()
    client = TestClient(main.app)
    response = client.post(
        f"/api/threads/{tid}/adhoc/expert/stream",
        json={
            "session_id": str(uuid.uuid4()),
            "provider_id": "claude",
            "tier": "free",
            "anchor_message_id": anchor_id,
        },
    )
    assert response.status_code == 200
    body = response.text.lower()
    assert "not configured" in body or "missing api key" in body or '"type": "error"' in body
    rows = list_thread_messages(tid)
    assert any("Four" in str(r.content or "") for r in rows)


def test_claude_stream_error_keeps_gpt_message(opinion_env, monkeypatch):
    tid, anchor_id = _seed_gpt_thread()

    async def _boom_route(*_a, **_k):
        raise TimeoutError("claude timed out")
        if False:
            yield ("", "", "")

    monkeypatch.setattr(
        "services.expert_opinion_service.route_request_stream",
        _boom_route,
    )
    client = TestClient(main.app)
    response = client.post(
        f"/api/threads/{tid}/adhoc/expert/stream",
        json={
            "session_id": str(uuid.uuid4()),
            "provider_id": "claude",
            "tier": "free",
            "anchor_message_id": anchor_id,
        },
    )
    assert response.status_code == 200
    assert "timed out" in response.text.lower() or '"type": "error"' in response.text
    rows = list_thread_messages(tid)
    assert any(r.provider == "gpt" and "Four" in str(r.content or "") for r in rows)


def test_direct_gpt_chat_still_works(opinion_env):
    client = TestClient(main.app)

    async def fake_chat(*_a, **_k):
        return {
            "thread_id": str(uuid.uuid4()),
            "response": "gpt-ok",
            "model_used": "m",
            "cost_usd": 0.0,
        }

    with patch.object(main, "handle_chat", new=AsyncMock(side_effect=fake_chat)):
        response = client.post(
            "/chat",
            json={"message": "hi", "tier": "free", "provider_id": "gpt"},
        )
    assert response.status_code == 200
    assert response.json().get("response") == "gpt-ok"


def test_main_adhoc_routes_do_not_import_capability_assert():
    import inspect
    import main as main_mod

    src = inspect.getsource(main_mod.api_adhoc_expert_stream)
    src2 = inspect.getsource(main_mod.api_adhoc_expert)
    assert "assert_provider_engine_active" not in src
    assert "assert_provider_engine_active" not in src2
    assert "assert_provider_engine_active" not in inspect.getsource(main_mod)


def test_gemini_assert_helper_still_exists_for_legacy():
    from services.engine_capability_gate import assert_provider_engine_active

    assert callable(assert_provider_engine_active)
