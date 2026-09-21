import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
import httpx
from services.providers import get_gateway_provider
from services.providers.base_provider import ProviderStreamEnd
from services.model_gateway import reset_circuit_breakers_for_tests
from services.inference.execution_context import begin_execution_context, set_execution_context

@pytest.fixture(autouse=True)
def reset(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    monkeypatch.setattr('services.providers.model_registry.datetime', Clock)
    monkeypatch.setattr('services.inference.gateway_meter.record_inference_call', AsyncMock(return_value={'persisted': True}))
    reset_circuit_breakers_for_tests()
    begin_execution_context(org_id="00000000-0000-0000-0000-000000000001", workspace_id="test", pipeline="chat")
    yield
    reset_circuit_breakers_for_tests()
    set_execution_context(None)

@pytest.mark.asyncio
@pytest.mark.parametrize('interrupted', [False, True])
async def test_deepseek_model_switch_persists_actual_model_same_thread(monkeypatch, interrupted):
    from services.chat_service import stream_chat_response
    from services.inference.usage_normalize import normalize_openai_usage

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key-not-a-secret")
    tid = uuid.uuid4()
    org = uuid.uuid4()
    persisted: list[dict] = []

    async def fake_stream(cx, *, model, message, tenant_id, system=None):
        yield f"reply-{model}"
        if interrupted:
            raise httpx.ReadTimeout('test timeout')
        yield ProviderStreamEnd(
            usage=normalize_openai_usage(
                {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
            )
        )

    def fake_sqlite(thread_id, *, user_text, assistant_content, provider=None):
        persisted.append(
            {
                "thread_id": thread_id,
                "user_text": user_text,
                "assistant_content": assistant_content,
                "provider": provider,
            }
        )
        return (len(persisted), len(persisted) + 10)

    with (
        patch("services.chat_service.resolve_thread_id", new=AsyncMock(return_value=tid)),
        patch("services.chat_service.is_project_setup_thread", return_value=False),
        patch(
            "services.chat_service.build_chat_message_with_thread_context",
            new=AsyncMock(side_effect=lambda _o, _t, m: m),
        ),
        patch("services.chat_service.inject_knowledge_few_shot", new=AsyncMock(side_effect=lambda _m, p: p)),
        patch("services.chat_service.apply_language_context", side_effect=lambda msg, _lang: msg),
        patch.object(get_gateway_provider("deepseek"), "stream_message", side_effect=fake_stream),
        patch("services.chat_service.persist_chat_exchange_sqlite", side_effect=fake_sqlite),
        patch("services.chat_service._schedule_chat_persist", side_effect=lambda coro: coro.close()),
        patch("services.chat_service.load_ready_files_context", new=AsyncMock(return_value=None)),
        patch("services.chat_service.run_copilot_preamble", new=AsyncMock(return_value=[])),
    ):
        for override in ("deepseek-flash", "deepseek-v4-pro", "deepseek-flash"):
            events = []
            async for _line in stream_chat_response(
                f"turn-{override}",
                "user-1",
                str(org),
                "free",
                thread_id=tid,
                provider_id="deepseek",
                model_override=override,
            ):
                events.append(json.loads(_line))
            assert any(e['type'] == 'chunk' for e in events)
            if interrupted:
                assert events[-1]['type'] == 'error'
                assert 'DeepSeek' in events[-1]['message']
                assert not any(e['type'] == 'done' for e in events)
                assert persisted == []
                continue
            done = next(e for e in events if e['type'] == 'done')
            assert done['provider_id'] == done['provider_used'] == 'deepseek'
            assert done['model_used'] == override
            assert done['thread_id'] == str(tid)

    if interrupted:
        return
    assert [str(row["thread_id"]) for row in persisted] == [str(tid)] * 3
    assert [row["provider"] for row in persisted] == ["deepseek", "deepseek", "deepseek"]
    models = []
    for row in persisted:
        payload = json.loads(row["assistant_content"])
        models.append(payload["model_used"])
        assert payload["provider_id"] == "deepseek"
        assert payload["cost_usd"] > 0
    assert models == ["deepseek-flash", "deepseek-v4-pro", "deepseek-flash"]
    costs = [json.loads(row["assistant_content"])["cost_usd"] for row in persisted]
    assert costs[0] == costs[2]
    assert costs[1] != costs[0]
