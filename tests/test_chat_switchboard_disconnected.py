"""Gate 1 — standard chat must not depend on Switchboard SQLite reads."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest
import main
from fastapi.testclient import TestClient
from services.global_service_store import init_global_service_schema


@pytest.fixture(autouse=True)
def _gate_a_customer():
    from tests.helpers_auth import patch_main_persistent_tenant

    with patch_main_persistent_tenant("00000000-0000-0000-0000-000000000001"):
        yield


def test_chat_succeeds_when_switchboard_store_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(tmp_path / "broken_switchboard.db"))
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    init_global_service_schema()

    def _boom(*_a, **_k):
        raise RuntimeError("system_main.db unavailable")

    monkeypatch.setattr("services.global_service_store.list_active_global_channels", _boom)
    monkeypatch.setattr("services.engine_capability_gate.list_active_global_channels", _boom)
    monkeypatch.setattr(
        "services.engine_capability_gate.is_provider_engine_active",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("gate must not run")),
    )

    client = TestClient(main.app)
    with patch.object(main, "handle_chat", new_callable=AsyncMock) as handle_mock:
        handle_mock.return_value = {
            "thread_id": "t-chat",
            "response": "ok",
            "model_used": "m",
            "cost_usd": 0.0,
        }
        response = client.post(
            "/chat",
            json={"message": "hi", "tier": "free", "provider_id": "gpt"},
        )

    assert response.status_code == 200
    assert response.json().get("response") == "ok"
    handle_mock.assert_awaited()


def test_chat_stream_succeeds_when_switchboard_store_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(tmp_path / "broken_switchboard_stream.db"))
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    init_global_service_schema()

    def _boom(*_a, **_k):
        raise RuntimeError("system_main.db unavailable")

    monkeypatch.setattr("services.global_service_store.list_active_global_channels", _boom)
    monkeypatch.setattr("services.engine_capability_gate.list_active_global_channels", _boom)

    async def _fake_stream(*_a, **_k):
        yield '{"type":"done"}\n'

    client = TestClient(main.app)
    with patch.object(main, "stream_chat_response", new=_fake_stream):
        response = client.post(
            "/chat/stream",
            json={"message": "hi stream", "tier": "free", "provider_id": "gpt"},
        )

    assert response.status_code == 200
    assert "CapabilityInactiveException" not in (response.text or "")


def test_execution_plan_module_has_no_switchboard_imports():
    import services.execution_plan as ep

    src = inspect.getsource(ep)
    assert "from services.engine_capability_gate" not in src
    assert "import services.engine_capability_gate" not in src
    assert "list_active_global_channels" not in src
    assert "is_provider_engine_active" not in src
    assert "catalog_key_for_provider" not in src
    assert "engine-grok" not in src
