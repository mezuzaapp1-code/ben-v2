"""Deterministic HTTP, dispatch, stream and metering contracts for DeepSeek."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from services import model_gateway as gateway
from services.inference.pricing import calculate_cost, resolve_pricing_snapshot
from services.providers import get_gateway_provider
from services.providers.base_provider import ProviderStreamEnd
from services.providers.deepseek_provider import DeepSeekProvider, deepseek_chat_payload, normalize_deepseek_usage
from services.providers import model_registry as registry
from services.providers.speaking_registry import gateway_for_provider_id
from services.tier1_models import tier1_model_for

MODELS = ['deepseek-flash', 'deepseek-v4-pro']
USAGE = {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120,
         'prompt_cache_hit_tokens': 40, 'completion_tokens_details': {'reasoning_tokens': 5}}


def sse(*events, done=True):
    return '\n\n'.join('data: ' + json.dumps(e) for e in events) + ('\n\ndata: [DONE]\n\n' if done else '\n\n')


def completion(stream):
    choice = {'finish_reason': 'stop', ('delta' if stream else 'message'): {'content': 'Hello'}}
    event = {'id': 'test-request', 'choices': [choice], 'usage': USAGE}
    return httpx.Response(200, text=sse(event)) if stream else httpx.Response(200, json=event)


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    gateway.reset_circuit_breakers_for_tests()
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-deepseek')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only-openai')
    monkeypatch.setattr(gateway, 'account_provider_attempt', AsyncMock(return_value={}))
    yield
    gateway.reset_circuit_breakers_for_tests()


@pytest.mark.parametrize('model', MODELS)
def test_registration_and_exact_dispatch(monkeypatch, model):
    monkeypatch.setenv('OPENAI_MODEL', 'wrong-model')
    assert gateway.normalize_chat_provider_id(' DeepSeek ') == 'deepseek'
    assert gateway_for_provider_id('deepseek') == 'deepseek'
    assert tier1_model_for('deepseek') == 'deepseek-flash'
    assert registry.resolve_api_model('deepseek', model) == model
    for tier in ('free', 'pro', 'enterprise'):
        assert gateway._attempts(tier, provider_id='deepseek', model_override=model) == [('deepseek', model)]
        assert gateway._attempts(tier, provider_id='deepseek') == [('deepseek', 'deepseek-flash')]
    assert gateway._CHAIN == ('openai', 'anthropic', 'google')
    with pytest.raises(ValueError):
        gateway.validate_chat_model_override('deepseek', 'gpt-4o')


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('model', MODELS)
@pytest.mark.asyncio
async def test_transport_serialization_usage_and_stream(model, stream):
    seen = []
    def handler(request):
        seen.append(request)
        return completion(stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        kwargs = dict(model=model, message='conversation and workspace context', tenant_id='tenant', system='BEN system')
        adapter = DeepSeekProvider()
        if stream:
            chunks = [x async for x in adapter.stream_message(client, **kwargs)]
            assert chunks[0] == 'Hello'
            assert isinstance(chunks[1], ProviderStreamEnd)
            result = chunks[1]
        else:
            result = await adapter.send_message(client, **kwargs)
            assert result.content == 'Hello'
            assert result.total_tokens == 120
            assert result.completion_tokens == 20
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == 'https://api.deepseek.com/chat/completions'
    assert request.headers['authorization'] == 'Bearer test-only-deepseek'
    body = json.loads(request.content)
    assert body == deepseek_chat_payload(model=model, messages=[{'role': 'system', 'content': 'BEN system'},
        {'role': 'user', 'content': 'conversation and workspace context'}], stream=stream)
    assert body['thinking'] == {'type': 'disabled'}
    assert 'tools' not in body and 'reasoning_effort' not in body
    assert result.provider_request_id == 'test-request'
    assert result.finish_reason == 'stop'
    assert result.usage.cached_input_tokens == 40
    assert result.usage.output_tokens == 15
    assert result.usage.reasoning_tokens == 5
    assert result.usage.total_tokens == 120


@pytest.mark.asyncio
async def test_stream_skips_keepalive_and_reasoning():
    text = ': keep-alive\n\n' + sse(
        {'choices': [{'delta': {'reasoning_content': 'not visible'}}]},
        {'choices': [{'delta': {'content': 'answer'}}]},
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': USAGE})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=text))) as client:
        chunks = [x async for x in DeepSeekProvider().stream_message(client, model=MODELS[0], message='hi', tenant_id='t')]
    assert chunks[0] == 'answer'
    assert len(chunks) == 2 and isinstance(chunks[-1], ProviderStreamEnd)


@pytest.mark.parametrize('text', [
    'data: invalid-json\n\n',
    sse({'error': {'message': 'remote sensitive text'}}),
    sse({'choices': [{'delta': {'content': 'partial'}}]}, done=False),
    sse({'choices': [{'delta': {}, 'finish_reason': 'aborted'}]}),
    sse({'choices': [{'delta': {}, 'finish_reason': 'insufficient_system_resource'}]}),
    sse({'choices': [{'delta': {'content': 'no terminal status'}}]}),
    sse({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}),
])
@pytest.mark.asyncio
async def test_broken_stream_is_not_success(text):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=text))) as client:
        with pytest.raises(ValueError) as exc:
            _ = [x async for x in DeepSeekProvider().stream_message(client, model=MODELS[0], message='hi', tenant_id='t')]
    assert 'remote sensitive text' not in str(exc.value)


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('status', [200, 401, 402, 429, 500, 503])
@pytest.mark.asyncio
async def test_gateway_http_dispatch_and_no_fallback(monkeypatch, stream, status):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        assert json.loads(request.content)['model'] == MODELS[1]
        return completion(stream) if status == 200 else httpx.Response(status, json={'error': {'message': 'remote sensitive text'}})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(gateway.httpx, 'AsyncClient', lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw))
    kwargs = dict(provider_id='deepseek', model_override=MODELS[1])
    if stream:
        chunks = [x async for x in gateway.route_request_stream('hi', 'tenant', 'enterprise', **kwargs)]
        assert all(x[2] == 'deepseek' for x in chunks)
        assert chunks[0][1] == (MODELS[1] if status == 200 else '')
        visible = chunks[0][0]
    else:
        result = await gateway.route_request('hi', 'tenant', 'enterprise', **kwargs)
        assert result['provider_used'] == 'deepseek'
        assert result['model_used'] == (MODELS[1] if status == 200 else '')
        visible = result['content']
    assert seen == ['https://api.deepseek.com/chat/completions']
    assert 'remote sensitive text' not in visible
    assert ('Hello' if status == 200 else 'DeepSeek') in visible


@pytest.mark.asyncio
async def test_missing_key_never_falls_back(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY')
    adapter = get_gateway_provider('deepseek')
    call = AsyncMock(side_effect=AssertionError('must not dispatch'))
    monkeypatch.setattr(adapter, 'send_message', call)
    result = await gateway.route_request('hi', 't', 'pro', provider_id='deepseek')
    assert result['model_used'] == ''
    assert 'DeepSeek' in result['content']
    call.assert_not_called()
    chunks = [x async for x in gateway.route_request_stream('hi', 't', 'pro', provider_id='deepseek')]
    assert len(chunks) == 1 and chunks[0][1:] == ('', 'deepseek')


def test_capabilities():
    from services.execution_plan import _provider_id_from_resource, _resolve_connector_id
    assert _provider_id_from_resource('deepseek-flash') == 'deepseek'
    assert _resolve_connector_id('deepseek-v4-pro') == 'deepseek_adapter'
    assert registry.model_capabilities('deepseek', MODELS[0]) == frozenset({'vision.analyze'})
    assert registry.model_capabilities('deepseek', MODELS[1]) == frozenset()
    messages = [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,eA=='}}]}]
    with pytest.raises(ValueError, match='does not support image'):
        deepseek_chat_payload(model=MODELS[1], messages=messages)
    assert deepseek_chat_payload(model=MODELS[0], messages=messages)['messages'] == messages


@pytest.mark.asyncio
async def test_empty_nonstream_response_is_an_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as client:
        with pytest.raises(ValueError, match='no answer content'):
            await DeepSeekProvider().send_message(client, model=MODELS[0], message='hi', tenant_id='t')


def test_invalid_usage_does_not_echo_remote_values():
    with pytest.raises(ValueError, match='^DeepSeek returned invalid usage$'):
        normalize_deepseek_usage({'prompt_tokens': 'remote sensitive text'})


@pytest.mark.parametrize('day,hour,peak', [(21, 0, False), (21, 1, True), (21, 3, True), (21, 4, False), (21, 6, True), (21, 9, True), (21, 10, False), (20, 6, False)])
@pytest.mark.parametrize('model,input_rate,output_rate,cached_rate', [(MODELS[0], .15e-6, .6e-6, .003e-6), (MODELS[1], .66e-6, 1.98e-6, .022e-6)])
def test_pricing_windows_and_no_reasoning_double_charge(monkeypatch, day, hour, peak, model, input_rate, output_rate, cached_rate):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, day, hour, tzinfo=timezone.utc)
    monkeypatch.setattr(registry, 'datetime', Clock)
    usage = normalize_deepseek_usage(USAGE)
    snapshot = resolve_pricing_snapshot(provider='deepseek', model=model, usage=usage)
    scale = 2 if peak else 1
    assert snapshot.input_usd_per_token == input_rate * scale
    assert snapshot.output_usd_per_token == output_rate * scale
    assert snapshot.cached_input_usd_per_token == cached_rate * scale
    assert calculate_cost(usage, snapshot).amount_usd == round(scale * (60 * input_rate + 40 * cached_rate + 20 * output_rate), 8)
