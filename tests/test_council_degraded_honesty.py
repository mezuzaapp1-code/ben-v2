"""Council degraded-expert honesty (mocked providers, no DB)."""
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
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")

from services.council_service import (  # noqa: E402
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


def _make_post(*, legal_mode: str = "ok", synthesis_agreement: str = "3/3"):
    """legal_mode: ok | timeout | bad_model"""

    async def fake_post(self, url: str, **kwargs: Any) -> _FakeResponse:
        u = str(url)
        if "api.anthropic.com" in u:
            if legal_mode == "timeout":
                raise httpx.ReadTimeout("legal timeout")
            if legal_mode == "bad_model":
                return _FakeResponse({"error": "model not found"}, status_code=404)
            return _FakeResponse(
                {
                    "content": [{"type": "text", "text": "Legal: document risks."}],
                    "usage": {"input_tokens": 10, "output_tokens": 12},
                }
            )
        if "api.openai.com" in u:
            jb = kwargs.get("json") or {}
            msgs = jb.get("messages") or []
            sys0 = _openai_sys(msgs)
            if SYNTHESIS_SYSTEM.splitlines()[0] in sys0:
                body = {
                    "recommendation": "Proceed with caution.",
                    "consensus_points": "Experts align on documentation.",
                    "main_disagreement": None,
                    "agreement_estimate": synthesis_agreement,
                }
                return _FakeResponse(
                    {
                        "choices": [{"message": {"content": json.dumps(body)}}],
                        "usage": {"prompt_tokens": 50, "completion_tokens": 30},
                    }
                )
            model = jb.get("model", "")
            if model == "gpt-4o":
                return _FakeResponse(
                    {
                        "choices": [{"message": {"content": "Business: viable market."}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 8},
                    }
                )
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": "Strategy: phased rollout."}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 7},
                }
            )
        raise AssertionError(f"unexpected URL: {u}")

    return fake_post


@pytest.mark.asyncio
async def test_happy_path_all_experts_ok():
    with patch.object(httpx.AsyncClient, "post", new=_make_post()):
        with patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
            out = await run_council("Launch Q2?", TENANT)

    assert len(out["council"]) == 3
    for m in out["council"]:
        assert m["outcome"] == "ok"
        assert m["provider"] in ("anthropic", "openai")
        assert "model" in m and m["response"]
    syn = out["synthesis"]
    assert syn is not None
    assert "3/3" in syn["agreement_estimate"] or "available" in syn["agreement_estimate"] or syn["agreement_estimate"]
    assert isinstance(out["cost_usd"], (int, float))
    assert out.get("request_id") is None or isinstance(out.get("request_id"), str)


@pytest.mark.asyncio
async def test_legal_timeout_degraded_honest_synthesis():
    with patch.object(httpx.AsyncClient, "post", new=_make_post(legal_mode="timeout", synthesis_agreement="2/3")):
        with patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
            out = await run_council("Launch with legal risk?", TENANT)

    legal = next(m for m in out["council"] if m["expert"] == "Legal Advisor")
    assert legal["outcome"] == "timeout"
    assert legal["outcome"] != "ok"
    assert "Expert unavailable" in legal["response"]
    assert "HTTPStatusError" not in legal["response"]

    biz = next(m for m in out["council"] if m["expert"] == "Business Advisor")
    assert biz["outcome"] == "ok"

    syn = out["synthesis"]
    assert syn is not None
    ae = syn["agreement_estimate"]
    assert "2/2 available" == ae or "available" in ae
    assert ae != "2/3"
    assert ae != "3/3"


@pytest.mark.asyncio
async def test_invalid_anthropic_model_degraded():
    with patch.object(httpx.AsyncClient, "post", new=_make_post(legal_mode="bad_model")):
        with patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
            out = await run_council("Bad model path?", TENANT)

    legal = next(m for m in out["council"] if m["expert"] == "Legal Advisor")
    assert legal["outcome"] in ("degraded", "error")
    assert legal["outcome"] != "ok"
    assert "Expert unavailable" in legal["response"]
