"""Council synthesis reasoning preservation (extended JSON, mocked providers)."""
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

from services.council_service import SYNTHESIS_SYSTEM, run_council  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"


class _FakeResponse:
    def __init__(self, data: dict[str, Any], status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)

    def json(self) -> dict[str, Any]:
        return self._data


def _openai_sys(msgs: list[dict[str, Any]] | None) -> str:
    if not msgs:
        return ""
    return str(msgs[0].get("content", ""))


def _synthesis_body(*, with_domains: bool, agreement: str = "3/3 available") -> dict[str, Any]:
    base = {
        "recommendation": "Adopt hybrid: closed core with open interfaces.",
        "consensus_points": "All agree on auditability.",
        "main_disagreement": None,
        "agreement_estimate": agreement,
    }
    if not with_domains:
        return base
    return {
        **base,
        "shared_recommendation": "Same as recommendation but explicit shared line.",
        "disagreement_points": (
            "Legal prioritizes liability caps; Strategy prioritizes speed-to-market rationale."
        ),
        "legal_reasoning": "License exposure favors documented boundaries.",
        "operational_reasoning": "Vendor cadence and support SLAs dominate run cost.",
        "strategic_reasoning": "Ecosystem adoption beats short-term margin.",
        "infrastructure_reasoning": None,
        "minority_or_unique_views": "One advisor stressed federated audit trails earlier.",
    }


def _make_post(*, with_domains: bool, gemini_fail: bool = False):
    async def fake_post(self, url: str, **kwargs: Any) -> _FakeResponse:
        u = str(url)
        if "api.anthropic.com" in u:
            return _FakeResponse(
                {
                    "content": [{"type": "text", "text": "Legal: OSS needs license compliance."}],
                    "usage": {"input_tokens": 10, "output_tokens": 12},
                }
            )
        if "generativelanguage.googleapis.com" in u:
            if gemini_fail:
                raise httpx.ReadTimeout("gemini timeout")
            return _FakeResponse(
                {
                    "candidates": [{"content": {"parts": [{"text": "Strategy: open core wins ecosystems."}]}}],
                    "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 10},
                }
            )
        if "api.openai.com" in u:
            jb = kwargs.get("json") or {}
            msgs = jb.get("messages") or []
            sys0 = _openai_sys(msgs)
            if SYNTHESIS_SYSTEM.splitlines()[0] in sys0:
                body = _synthesis_body(with_domains=with_domains, agreement="3/3 available")
                return _FakeResponse(
                    {
                        "choices": [{"message": {"content": json.dumps(body)}}],
                        "usage": {"prompt_tokens": 60, "completion_tokens": 40},
                    }
                )
            if jb.get("model") == "gpt-4o":
                return _FakeResponse(
                    {
                        "choices": [{"message": {"content": "Business: operational overhead of hybrid."}}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 9},
                    }
                )
            return _FakeResponse(
                {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            )
        raise AssertionError(f"unexpected URL: {u}")

    return fake_post


@pytest.mark.asyncio
async def test_three_provider_synthesis_preserves_domain_reasoning():
    with patch.object(httpx.AsyncClient, "post", new=_make_post(with_domains=True)):
        with patch(
            "services.council_service._persist_council_thread_if_needed",
            new=lambda *a, **k: asyncio.sleep(0),
        ), patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
            out = await run_council(
                "Should we open-source our governance layer vs keep it proprietary?",
                TENANT,
            )

    assert out["synthesis"] is not None
    syn = out["synthesis"]
    assert syn.get("legal_reasoning")
    assert syn.get("strategic_reasoning")
    assert syn.get("operational_reasoning")
    assert syn.get("disagreement_points")
    assert "HTTPStatusError" not in json.dumps(out)
    assert isinstance(out.get("request_id"), (str, type(None)))


@pytest.mark.asyncio
async def test_degraded_gemini_honest_no_fake_three_panel_consensus():
    with patch.object(httpx.AsyncClient, "post", new=_make_post(with_domains=True, gemini_fail=True)):
        with patch(
            "services.council_service._persist_council_thread_if_needed",
            new=lambda *a, **k: asyncio.sleep(0),
        ), patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
            out = await run_council("Open vs closed governance?", TENANT)

    strat = next(m for m in out["council"] if m["expert"] == "Strategy Advisor")
    assert strat["outcome"] == "timeout"
    syn = out["synthesis"]
    assert syn is not None
    assert syn["agreement_estimate"] == "2/2 available"
    # Must not claim 3/3 in agreement string when one expert down
    assert "3/3" not in str(syn.get("agreement_estimate", ""))


@pytest.mark.asyncio
async def test_backward_compat_minimal_synthesis_json():
    async def fake_post(self, url: str, **kwargs: Any) -> _FakeResponse:
        u = str(url)
        if "api.anthropic.com" in u:
            return _FakeResponse(
                {"content": [{"type": "text", "text": "L"}], "usage": {"input_tokens": 1, "output_tokens": 1}}
            )
        if "generativelanguage.googleapis.com" in u:
            return _FakeResponse(
                {
                    "candidates": [{"content": {"parts": [{"text": "S"}]}}],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
                }
            )
        if "api.openai.com" in u:
            jb = kwargs.get("json") or {}
            msgs = jb.get("messages") or []
            if SYNTHESIS_SYSTEM.splitlines()[0] in _openai_sys(msgs):
                return _FakeResponse(
                    {
                        "choices": [
                            {"message": {"content": json.dumps({"recommendation": "R", "consensus_points": "C", "main_disagreement": None, "agreement_estimate": "3/3 available"})}}
                        ],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
                    }
                )
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": "B"}}],
                    "usage": {"prompt_tokens": 1, "output_tokens": 1},
                }
            )
        raise AssertionError(u)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        with patch(
            "services.council_service._persist_council_thread_if_needed",
            new=lambda *a, **k: asyncio.sleep(0),
        ), patch("services.council_service._persist_synthesis_ko", new=lambda *a, **k: asyncio.sleep(0)):
            out = await run_council("Q?", TENANT)

    syn = out["synthesis"]
    assert syn["recommendation"] == "R"
    assert "legal_reasoning" not in syn or syn.get("legal_reasoning") is None
    assert out["cost_usd"] is not None
