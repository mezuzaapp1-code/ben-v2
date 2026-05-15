"""Pre-merge checks for council + synthesis (uses HTTP mocks; requires DATABASE_URL + alembic head).

Run after: docker postgres + `alembic upgrade head` with same DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

# DATABASE_URL must be set before importing database.*
if not os.environ.get("DATABASE_URL"):
    print("Set DATABASE_URL (e.g. postgresql+asyncpg://user:pass@127.0.0.1:55432/db)", file=sys.stderr)
    sys.exit(1)

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from database.connection import get_db_session  # noqa: E402
from services.council_service import SYNTHESIS_SYSTEM, run_council  # noqa: E402

TENANT = "00000000-0000-0000-0000-000000000001"


class _FakeResponse:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._data


def _openai_body(messages: list[dict[str, Any]] | None) -> str:
    if not messages:
        return ""
    return str(messages[0].get("content", ""))


@contextmanager
def _capture_logs(name: str, level: int = logging.WARNING):
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setLevel(level)
    log = logging.getLogger(name)
    old = log.level
    log.setLevel(level)
    log.addHandler(h)
    try:
        yield buf
    finally:
        log.removeHandler(h)
        log.setLevel(old)


def _make_fake_post(*, fail_synthesis: bool):
    async def fake_post(self, url: str, **kwargs: Any) -> _FakeResponse:
        u = str(url)
        if "api.anthropic.com" in u:
            return _FakeResponse(
                {
                    "content": [{"type": "text", "text": "Legal: low risk if documented."}],
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                }
            )
        if "api.openai.com" in u:
            jb = kwargs.get("json") or {}
            msgs = jb.get("messages") or []
            sys0 = _openai_body(msgs)
            if SYNTHESIS_SYSTEM.splitlines()[0] in sys0 or "synthesize expert opinions" in sys0:
                if fail_synthesis:
                    raise httpx.ReadTimeout("forced synthesis failure")
                body = {
                    "recommendation": "Proceed with a pilot and legal review.",
                    "consensus_points": "All experts agree documentation matters.",
                    "main_disagreement": None,
                    "agreement_estimate": "3/3",
                }
                return _FakeResponse(
                    {
                        "choices": [{"message": {"content": json.dumps(body)}}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                    }
                )
            model = jb.get("model", "")
            if model == "gpt-4o":
                return _FakeResponse(
                    {
                        "choices": [{"message": {"content": "Business: strong unit economics."}}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                    }
                )
            if model == "gpt-4o-mini":
                return _FakeResponse(
                    {
                        "choices": [{"message": {"content": "Strategy: phase rollout to reduce risk."}}],
                        "usage": {"prompt_tokens": 15, "completion_tokens": 12},
                    }
                )
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
            )
        raise AssertionError(f"unexpected URL: {u}")

    return fake_post


async def _assert_ko_row() -> None:
    org = TENANT
    async with get_db_session() as session:
        await session.execute(text("SELECT set_config('app.current_org_id', :v, true)"), {"v": org})
        row = (
            (
                await session.execute(
                    text(
                        """
                        SELECT type, status, confidence, pg_typeof(content)::text AS ctype, content
                        FROM ben.knowledge_objects
                        WHERE org_id = CAST(:org AS uuid) AND type = 'synthesis'
                        ORDER BY created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"org": org},
                )
            )
            .mappings()
            .one_or_none()
        )
        assert row is not None, "expected a synthesis knowledge_object row"
        assert row["type"] == "synthesis"
        assert row["status"] == "evolving"
        assert row["confidence"] is None
        ctype = str(row["ctype"])
        assert "jsonb" in ctype.lower(), f"content should be jsonb, got {ctype}"
        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        assert isinstance(content, dict)
        assert content.get("agreement_estimate") == "3/3"


async def _main() -> None:
    # 2) Success path
    fake_post = _make_fake_post(fail_synthesis=False)
    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        out = await run_council("Should we launch feature X in Q2?", TENANT)

    assert len(out["council"]) == 3, "three experts"
    assert all("response" in m and m["response"] for m in out["council"]), "expert bodies"
    assert all(m.get("outcome") == "ok" for m in out["council"]), "outcomes"
    assert all("provider" in m and "model" in m for m in out["council"]), "metadata"
    syn = out.get("synthesis")
    assert syn is not None and isinstance(syn, dict), "synthesis should be present"
    assert syn.get("recommendation")
    assert "cost_usd" in out and out["cost_usd"] is not None and float(out["cost_usd"]) >= 0

    await _assert_ko_row()

    # 3) Forced synthesis failure + WARNING
    fail_post = _make_fake_post(fail_synthesis=True)
    with _capture_logs("services.council_service") as buf2:
        with patch.object(httpx.AsyncClient, "post", new=fail_post):
            out2 = await run_council("Second question: go or no-go?", TENANT)

    assert len(out2["council"]) == 3
    assert all(m.get("response") for m in out2["council"])
    assert out2.get("synthesis") is None
    log_text = buf2.getvalue()
    assert "council synthesis" in log_text.lower() or "synthesis failed" in log_text.lower(), (
        f"expected WARNING log about synthesis failure; got:\n{log_text!r}"
    )

    print("verify_council_prerelease: OK")
    print(json.dumps({"sample_success": {"synthesis": syn, "cost_usd": out["cost_usd"]}}, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
