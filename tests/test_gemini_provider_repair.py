"""Bounded Gemini provider repair: retired ids, identity, transport, CB, no fallthrough."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from services.model_gateway import (  # noqa: E402
    _CHAIN,
    reset_circuit_breakers_for_tests,
    resolve_dispatch_model,
    route_request,
    route_request_stream,
    selected_chat_attempt,
    validate_chat_model_override,
)
from services.ops.failure_classification import FAILURE_CONFIG_ERROR, classify_failure
from services.providers import get_gateway_provider
from services.providers.base_provider import ProviderSendResult, ProviderStreamEnd
from services.providers.gemini_provider import (
    GEMINI_CHAT_MODELS,
    GEMINI_FAST_MODEL,
    GEMINI_RETIRED_MODELS,
    GeminiProvider,
    assert_live_gemini_model,
    resolve_gemini_default_model,
)
from services.providers.model_registry import (
    allowed_models,
    is_exact_dispatch,
    is_registered_model,
    model_identity,
    resolve_api_model,
)
from services.providers.provider_errors import format_chat_provider_error
from services.tier1_models import TIER1_GEMINI_MODEL

TENANT = "00000000-0000-0000-0000-000000000001"
_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"


@pytest.fixture(autouse=True)
def _reset_cb():
    reset_circuit_breakers_for_tests()
    yield
    reset_circuit_breakers_for_tests()


def _http_status_error(code: int, url: str = "https://generativelanguage.googleapis.com/v1beta/models/x") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", url)
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


class _GenerateResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }


class _RecordingClient:
    def __init__(self):
        self.posts: list[dict] = []

    async def post(self, url, *, params=None, headers=None, json=None):
        self.posts.append({"url": url, "params": params, "json": json})
        return _GenerateResponse()


class _StreamResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _StreamContext:
    def __init__(self, lines: list[str]):
        self._lines = lines

    async def __aenter__(self):
        return _StreamResponse(self._lines)

    async def __aexit__(self, *_args):
        return None


class _RecordingStreamClient:
    def __init__(self, lines: list[str]):
        self.calls: list[dict] = []
        self._lines = lines

    def stream(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "params": kwargs.get("params")})
        return _StreamContext(self._lines)


def test_gemini_15_flash_is_not_selectable_or_registered():
    assert "gemini-1.5-flash" not in GEMINI_CHAT_MODELS
    assert "gemini-1.5-flash" in GEMINI_RETIRED_MODELS
    assert not is_registered_model("google", "gemini-1.5-flash")
    assert ("google", "gemini-1.5-flash") not in allowed_models()
    with pytest.raises(ValueError, match="not registered"):
        validate_chat_model_override("gemini", "gemini-1.5-flash")
    with pytest.raises(ValueError, match="not registered"):
        resolve_dispatch_model("google", "gemini-1.5-flash")


@pytest.mark.parametrize("retired", sorted(GEMINI_RETIRED_MODELS))
def test_retired_gemini_ids_cannot_be_dispatched(retired):
    assert not is_registered_model("google", retired)
    with pytest.raises(ValueError, match="retired|not registered"):
        assert_live_gemini_model(retired)
    with pytest.raises(ValueError, match="not registered"):
        resolve_api_model("google", retired)


@pytest.mark.asyncio
async def test_provider_refuses_to_http_retired_gemini():
    client = _RecordingClient()
    with pytest.raises(ValueError, match="retired"):
        await GeminiProvider().send_message(
            client,  # type: ignore[arg-type]
            model="gemini-1.5-flash",
            message="hi",
            tenant_id=TENANT,
        )
    assert client.posts == []


def test_canonical_gemini_identity_matches_dispatched_api_id(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-2.5-flash")
    assert TIER1_GEMINI_MODEL == GEMINI_FAST_MODEL == "gemini-3.8-flash"
    assert GEMINI_CHAT_MODELS == ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash")
    for canonical in GEMINI_CHAT_MODELS:
        ident = model_identity("google", canonical)
        assert ident.get("exact_dispatch") is True
        assert ident.get("api_model") == canonical
        assert is_exact_dispatch("google", canonical)
        assert resolve_api_model("google", canonical) == canonical
        assert resolve_dispatch_model("google", canonical) == canonical
        gw, selected = selected_chat_attempt(
            "free", provider_id="gemini", model_override=canonical
        )
        assert gw == "google"
        assert selected == canonical
        assert resolve_dispatch_model(gw, selected) == canonical


def test_env_does_not_remap_a_selected_gemini_model(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-1.5-flash")
    assert resolve_dispatch_model("google", "gemini-3.5-flash") == "gemini-3.5-flash"
    assert resolve_dispatch_model("google", "gemini-3.8-flash") == "gemini-3.8-flash"


def test_retired_env_model_does_not_change_default_ui_identity(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GOOGLE_MODEL", raising=False)
    assert resolve_gemini_default_model() == "gemini-3.8-flash"
    monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-flash")
    assert resolve_gemini_default_model() == "gemini-3.8-flash"
    gw, model = selected_chat_attempt("free", provider_id="gemini")
    assert gw == "google"
    assert model == "gemini-3.8-flash"
    assert resolve_dispatch_model(gw, model) == "gemini-3.8-flash"


def test_registered_env_default_is_explicit_and_identity_true(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    assert resolve_gemini_default_model() == "gemini-2.5-flash"
    gw, model = selected_chat_attempt("free", provider_id="gemini")
    assert model == "gemini-2.5-flash"
    assert resolve_dispatch_model(gw, model) == "gemini-2.5-flash"


def test_invalid_env_override_is_logged_without_the_value(monkeypatch, caplog):
    secretish = "sk-this-must-never-appear-in-logs"
    monkeypatch.setenv("GEMINI_MODEL", secretish)
    with caplog.at_level(logging.WARNING, logger="ben.ops"):
        gw, model = selected_chat_attempt("free", provider_id="gemini")
    assert model == "gemini-3.8-flash"
    assert gw == "google"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert secretish not in joined
    assert secretish not in caplog.text
    assert any("Ignoring GEMINI_MODEL/GOOGLE_MODEL" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_supported_gemini_dispatch_url_is_v1beta_generate_content(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-a-secret")
    client = _RecordingClient()
    result = await GeminiProvider().send_message(
        client,  # type: ignore[arg-type]
        model="gemini-3.8-flash",
        message="hi",
        tenant_id=TENANT,
    )
    assert result.content == "ok"
    assert client.posts[0]["url"] == _GENERATE_URL.format(model="gemini-3.8-flash")
    assert "/v1beta/models/gemini-3.8-flash:generateContent" in client.posts[0]["url"]
    assert client.posts[0]["url"].startswith("https://generativelanguage.googleapis.com/")


@pytest.mark.asyncio
async def test_gemini_streaming_url_and_chunks(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-a-secret")
    lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"hel"}]}}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"lo"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":2}}',
    ]
    client = _RecordingStreamClient(lines)
    chunks = [
        item
        async for item in GeminiProvider().stream_message(
            client,  # type: ignore[arg-type]
            model="gemini-3.5-flash",
            message="hi",
            tenant_id=TENANT,
        )
    ]
    assert client.calls[0]["url"] == _STREAM_URL.format(model="gemini-3.5-flash")
    assert client.calls[0]["method"] == "POST"
    assert "/v1beta/models/gemini-3.5-flash:streamGenerateContent" in client.calls[0]["url"]
    assert chunks[0] == "hel"
    assert chunks[1] == "lo"
    assert isinstance(chunks[-1], ProviderStreamEnd)
    assert chunks[-1].finish_reason == "STOP"


def test_gemini_404_is_config_error_and_model_unavailable_message():
    exc = _http_status_error(404)
    assert classify_failure(exc) == FAILURE_CONFIG_ERROR
    assert (
        format_chat_provider_error("google", exc, timeout_s=25)
        == "Gemini model is not available (HTTP 404)"
    )


@pytest.mark.asyncio
async def test_explicit_gemini_404_does_not_fall_through(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    seen: list[str] = []

    async def fail_google(cx, *, model, message, tenant_id, system=None, user_content=None):
        seen.append(f"google:{model}")
        raise _http_status_error(404, url=_GENERATE_URL.format(model=model))

    async def other(name):
        async def _send(cx, *, model, message, tenant_id, system=None, user_content=None):
            seen.append(f"{name}:{model}")
            return ProviderSendResult.from_token_counts("should-not-run", 1, 1)

        return _send

    with (
        patch.object(get_gateway_provider("google"), "send_message", side_effect=fail_google),
        patch.object(get_gateway_provider("openai"), "send_message", side_effect=await other("openai")),
        patch.object(get_gateway_provider("anthropic"), "send_message", side_effect=await other("anthropic")),
        patch.object(get_gateway_provider("xai"), "send_message", side_effect=await other("xai")),
    ):
        out = await route_request(
            "hi",
            TENANT,
            "pro",
            provider_id="gemini",
            model_override="gemini-3.8-flash",
        )
    assert seen == ["google:gemini-3.8-flash"]
    assert out["content"] == "Gemini model is not available (HTTP 404)"
    assert out["provider_used"] == "google"
    assert "openai" not in seen[0] if seen else True
    assert _CHAIN == ("openai", "anthropic", "google")


@pytest.mark.asyncio
async def test_gemini_circuit_breaker_stays_on_google_then_unavailable(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    seen: list[str] = []

    async def fail_google(cx, *, model, message, tenant_id, system=None, user_content=None):
        seen.append("google")
        raise _http_status_error(404, url=_GENERATE_URL.format(model=model))

    async def fail_if_called(name):
        async def _send(cx, *, model, message, tenant_id, system=None, user_content=None):
            seen.append(name)
            raise AssertionError(f"{name} must not be used as Gemini fallback")

        return _send

    with (
        patch.object(get_gateway_provider("google"), "send_message", side_effect=fail_google),
        patch.object(get_gateway_provider("openai"), "send_message", side_effect=await fail_if_called("openai")),
        patch.object(get_gateway_provider("anthropic"), "send_message", side_effect=await fail_if_called("anthropic")),
        patch.object(get_gateway_provider("xai"), "send_message", side_effect=await fail_if_called("xai")),
    ):
        for _ in range(3):
            out = await route_request(
                "hi", TENANT, "free", provider_id="gemini", model_override="gemini-3.8-flash"
            )
            assert out["content"] == "Gemini model is not available (HTTP 404)"
        fourth = await route_request(
            "hi", TENANT, "free", provider_id="gemini", model_override="gemini-3.8-flash"
        )
    assert seen == ["google", "google", "google"]
    assert fourth["content"] == "No provider available"
    assert fourth["model_used"] == ""


@pytest.mark.asyncio
async def test_explicit_gemini_stream_does_not_fall_through(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen: list[str] = []

    async def fail_google(cx, *, model, message, tenant_id, system=None, user_content=None):
        seen.append(f"google:{model}")
        raise _http_status_error(404)
        yield  # pragma: no cover

    async def other_stream(cx, *, model, message, tenant_id, system=None, user_content=None):
        seen.append(f"openai:{model}")
        yield "nope"

    with (
        patch.object(get_gateway_provider("google"), "stream_message", side_effect=fail_google),
        patch.object(get_gateway_provider("openai"), "stream_message", side_effect=other_stream),
    ):
        chunks = []
        async for item in route_request_stream(
            "hi", TENANT, "free", provider_id="gemini", model_override="gemini-3.5-flash"
        ):
            chunks.append(item)
    assert seen == ["google:gemini-3.5-flash"]
    assert chunks[0][0] == "Gemini model is not available (HTTP 404)"
    assert chunks[0][2] == "google"
