"""Active Context Focus query ceiling vs unbounded /chat/stream messages."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from routers.knowledge import ACTIVE_ATTENTION_QUERY_MAX_CHARS  # noqa: E402
from tests.helpers_auth import AUTH_HEADER, patch_main_persistent_tenant  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"
BETA_CODE = "basalt-closed-beta-2026"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "true")
    monkeypatch.setenv("BEN_BETA_PASSCODE", BETA_CODE)
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", TENANT)
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "projects"))
    yield


def _beta_headers(alias: str = "Alon") -> dict[str, str]:
    return {
        "X-Basalt-Beta-Passcode": BETA_CODE,
        "X-Basalt-Beta-Alias": alias,
        **AUTH_HEADER,
    }


def test_server_cap_matches_frontend_constant():
    frontend = (Path(__file__).resolve().parents[1] / "frontend/src/lib/attentionQuery.js").read_text(
        encoding="utf-8"
    )
    assert "ATTENTION_QUERY_SERVER_MAX_CHARS = 4096" in frontend
    assert ACTIVE_ATTENTION_QUERY_MAX_CHARS == 4096


def test_chat_body_has_no_message_length_cap():
    from main import ChatBody

    body = ChatBody(message="m" * 20_000, tier="pro", provider_id="gpt")
    assert len(body.message) == 20_000


def test_active_attention_short_and_bound_queries_ok(tmp_path, monkeypatch):
    from services.knowledge_store import HEAD_CODE, insert_context_record

    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "projects"))
    slug = "focus-query-demo"
    insert_context_record(
        slug,
        head=HEAD_CODE,
        title="handle_chat",
        content="async def handle_chat(message): ...",
    )
    client = TestClient(main.app)
    short = client.get(
        f"/api/projects/{slug}/threads/thread-alpha/active-attention",
        params={"query": "x" * 100},
        headers=_beta_headers(),
    )
    assert short.status_code == 200, short.text
    bound = client.get(
        f"/api/projects/{slug}/threads/thread-alpha/active-attention",
        params={"query": "a" * ACTIVE_ATTENTION_QUERY_MAX_CHARS},
        headers=_beta_headers(),
    )
    assert bound.status_code == 200, bound.text


def test_active_attention_over_cap_is_422_string_too_long(tmp_path, monkeypatch):
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "projects"))
    client = TestClient(main.app)
    res = client.get(
        "/api/projects/focus-query-demo/threads/thread-alpha/active-attention",
        params={"query": "b" * (ACTIVE_ATTENTION_QUERY_MAX_CHARS + 1)},
        headers=_beta_headers(),
    )
    assert res.status_code == 422
    detail = res.json().get("detail")
    assert isinstance(detail, list)
    assert any(item.get("type") == "string_too_long" for item in detail)


def test_chat_stream_keeps_full_long_message_for_each_engine():
    captured: list[tuple[str | None, str]] = []

    async def _fake_stream(message, *_args, **kwargs):
        captured.append((kwargs.get("provider_id"), message))
        yield json.dumps({"type": "chunk", "content": "ok"}) + "\n"
        yield json.dumps({"type": "done", "response": "ok", "thread_id": str(uuid.uuid4())}) + "\n"

    full = "HEAD-CONTEXT\n" + ("z" * 19_000) + "\nWhat is the Data Hall opening width?"
    assert len(full) > 19_000
    client = TestClient(main.app)
    with patch_main_persistent_tenant(TENANT), patch("main.stream_chat_response", _fake_stream):
        for provider in ("gpt", "claude", "gemini"):
            res = client.post(
                "/chat/stream",
                json={"message": full, "tier": "pro", "provider_id": provider},
                headers=_beta_headers(),
            )
            assert res.status_code == 200, (provider, res.text)

    by_provider = dict(captured)
    assert set(by_provider) >= {"gpt", "claude", "gemini"}
    for provider, message in captured:
        assert message == full, f"{provider} must receive the full chat message"
