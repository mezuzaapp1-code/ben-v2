"""Council Strategy Advisor via Google Gemini (mocked HTTP, no DB)."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import patch

import httpx
import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from services.council_service import (  # noqa: E402
    GEMINI_MODEL_DEFAULT,
    SYNTHESIS_SYSTEM,
    run_council,
)

TENANT = "00000000-0000-0000-0000-000000000001"


class _FakeResponse:
    def __init__(self, data: dict[str, Any], status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self) -> dict[str, Any]:
        return self._data


def _openai_sys(messages: list[dict[str, Any]] | None) -> str:
    if not messages:
        return ""
    return str(messages[0].get("content", ""))


def _make_post(*, gemini_mode: str = "ok"):
    """gemini_mode: ok | timeout | bad_model"""

    async def fake_post(self, url: str, **kwargs: Any) -> _FakeResponse:
        u = str(url)
        if "api.anthropic.com" in u:
            return _FakeResponse(
                {
                    "content": [{"type": "text", "text": "Legal: compliance noted."}],
                    "usage": {"input_tokens": 10, "output_tokens": 12},
                }
            )
        if "generativelanguage.googleapis.com" in u:
            if gemini_mode == "timeout":
                raise httpx.ReadTimeout("gemini timeout")
            if gemini_mode == "bad_model":
                return _FakeResponse({"error": "model not found"}, status_code=404)
            return _FakeResponse(
                {
                    "candidates": [{"content": {"parts": [{"text": "Strategy: long-term phased plan."}]}}],
                    "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 10},
                }
            )
        if "api.openai.com" in u:
            jb = kwargs.get("json") or {}
            msgs = jb.get("messages") or []
            sys0 = _openai_sys(msgs)
            if SYNTHESIS_SYSTEM.splitlines()[0] in sys0:
                body = {
                    "recommendation": "Move forward carefully.",
                    "consensus_points": "Available experts align.",
                    "main_disagreement": None,
                    "agreement_estimate": "3/3",
                }
                return _FakeResponse(
                    {
                        "choices": [{"message": {"content": json.dumps(body)}}],
                        "usage": {"prompt_tokens": 40, "completion_tokens": 20},
                    }
                )
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": "Business: strong growth path."}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 8},
                }
            )
        raise AssertionError(f"unexpected URL: {u}")

    return fake_post


@pytest.mark.asyncio
async def test_happy_path_three_provider_diversity():
    with patch.object(httpx.AsyncClient, "post", new=_make_post()):
        with patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
            out = await run_council("Gemini strategy smoke?", TENANT)

    providers = {m["expert"]: m["provider"] for m in out["council"]}
    assert providers["Legal Advisor"] == "anthropic"
    assert providers["Business Advisor"] == "openai"
    assert providers["Strategy Advisor"] == "google"

    strat = next(m for m in out["council"] if m["expert"] == "Strategy Advisor")
    assert strat["outcome"] == "ok"
    assert strat["model"] == GEMINI_MODEL_DEFAULT
    assert "HTTPStatusError" not in strat["response"]

    assert out.get("synthesis") is not None
    assert isinstance(float(out["cost_usd"]), float)


@pytest.mark.asyncio
async def test_gemini_timeout_degraded_honest_synthesis():
    with patch.object(httpx.AsyncClient, "post", new=_make_post(gemini_mode="timeout")):
        with patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
            out = await run_council("Strategy timeout?", TENANT)

    strat = next(m for m in out["council"] if m["expert"] == "Strategy Advisor")
    assert strat["provider"] == "google"
    assert strat["outcome"] == "timeout"
    assert "Expert unavailable" in strat["response"]

    syn = out["synthesis"]
    assert syn is not None
    assert syn["agreement_estimate"] == "2/2 available"


@pytest.mark.asyncio
async def test_missing_google_api_key_degraded():
    with patch.dict(os.environ, {"GOOGLE_API_KEY": ""}, clear=False):
        with patch.object(httpx.AsyncClient, "post", new=_make_post()):
            with patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
                out = await run_council("No google key?", TENANT)

    strat = next(m for m in out["council"] if m["expert"] == "Strategy Advisor")
    assert strat["provider"] == "google"
    assert strat["outcome"] == "degraded"
    assert "GOOGLE" in strat["response"] or "missing" in strat["response"].lower()
    syn = out["synthesis"]
    assert syn is not None
    assert "2/2 available" in syn["agreement_estimate"]
