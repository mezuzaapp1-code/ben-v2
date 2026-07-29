"""Presentation translation cache identity and English fallback."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")
os.environ.setdefault("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")

from services.news.presentation_translate import (  # noqa: E402
    TRANSLATION_ENGINE_VERSION,
    normalize_locale,
    source_text_hash,
    translate_presentation_fields,
)


def test_normalize_locale():
    assert normalize_locale("he-IL") == "he"
    assert normalize_locale("EN") == "en"
    assert normalize_locale("fr") == "en"


def test_source_text_hash_stable():
    assert source_text_hash("Hello") == source_text_hash("Hello")
    assert source_text_hash("Hello") != source_text_hash("Hello ")


@pytest.mark.asyncio
async def test_english_skips_gateway():
    with patch(
        "services.news.presentation_translate._call_translate",
        new_callable=AsyncMock,
    ) as call:
        out = await translate_presentation_fields(
            event_id=str(uuid.uuid4()),
            package_version=1,
            locale="en",
            fields={"headline": "H", "summary": "S"},
        )
    call.assert_not_awaited()
    assert out["texts"]["headline"] == "H"
    assert out["original_locale_indicator"] is False
    assert out["translation_engine_version"] == TRANSLATION_ENGINE_VERSION


@pytest.mark.asyncio
async def test_hebrew_uses_cache_then_skips_inference():
    event_id = str(uuid.uuid4())
    fields = {"headline": "OpenAI ships model", "summary": "A brief summary."}

    with (
        patch(
            "services.news.presentation_translate._lookup_cached",
            new_callable=AsyncMock,
            side_effect=["כותרת", "סיכום"],
        ) as lookup,
        patch(
            "services.news.presentation_translate._call_translate",
            new_callable=AsyncMock,
        ) as call,
        patch(
            "services.news.presentation_translate._store_cached",
            new_callable=AsyncMock,
        ) as store,
    ):
        out = await translate_presentation_fields(
            event_id=event_id,
            package_version=3,
            locale="he",
            fields=fields,
        )

    assert out["texts"]["headline"] == "כותרת"
    assert out["texts"]["summary"] == "סיכום"
    assert out["fallback_fields"] == []
    assert out["translation_status"] == "cached"
    assert out["field_translation_status"] == {
        "headline": "cached",
        "summary": "cached",
    }
    call.assert_not_awaited()
    store.assert_not_awaited()
    assert lookup.await_count == 2


@pytest.mark.asyncio
async def test_hebrew_failure_falls_back_to_english():
    event_id = str(uuid.uuid4())
    with (
        patch(
            "services.news.presentation_translate._lookup_cached",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "services.news.presentation_translate._call_translate",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "services.news.presentation_translate._store_cached",
            new_callable=AsyncMock,
        ),
    ):
        out = await translate_presentation_fields(
            event_id=event_id,
            package_version=1,
            locale="he",
            fields={"headline": "Headline", "summary": "Summary"},
        )
    assert out["texts"]["headline"] == "Headline"
    assert out["texts"]["summary"] == "Summary"
    assert set(out["fallback_fields"]) == {"headline", "summary"}
    assert out["original_locale_indicator"] is True
    assert out["translation_status"] == "fallback_en"
    assert out["field_translation_status"]["headline"] == "fallback_en"


@pytest.mark.asyncio
async def test_package_version_misses_prior_cache():
    """Cache identity includes package_version — v2 does not reuse v1 rows."""
    event_id = str(uuid.uuid4())
    fields = {"headline": "Same headline", "summary": "Same summary"}
    lookups: list[int] = []

    async def _lookup(*, package_version, **_kwargs):
        lookups.append(package_version)
        return None

    with (
        patch(
            "services.news.presentation_translate._lookup_cached",
            new=_lookup,
        ),
        patch(
            "services.news.presentation_translate._call_translate",
            new_callable=AsyncMock,
            return_value="תרגום",
        ),
        patch(
            "services.news.presentation_translate._store_cached",
            new_callable=AsyncMock,
        ) as store,
    ):
        out1 = await translate_presentation_fields(
            event_id=event_id,
            package_version=1,
            locale="he",
            fields=fields,
        )
        out2 = await translate_presentation_fields(
            event_id=event_id,
            package_version=2,
            locale="he",
            fields=fields,
        )

    assert lookups == [1, 1, 2, 2]
    assert out1["translation_status"] == "generated"
    assert out2["translation_status"] == "generated"
    assert store.await_count == 4


@pytest.mark.asyncio
async def test_translate_uses_gateway_with_capability_key():
    event_id = str(uuid.uuid4())
    begin_calls: list[dict] = []

    async def _fake_completion(**_kwargs):
        return {"choices": [{"message": {"content": "כותרת"}}]}

    def _begin(**kwargs):
        begin_calls.append(kwargs)

    with (
        patch(
            "services.news.presentation_translate._lookup_cached",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "services.news.presentation_translate._store_cached",
            new_callable=AsyncMock,
        ),
        patch(
            "services.news.presentation_translate.begin_execution_context",
            side_effect=_begin,
        ),
        patch(
            "services.news.presentation_translate.set_execution_context",
        ),
        patch(
            "services.news.presentation_translate.accounted_openai_chat_completion",
            new_callable=AsyncMock,
            side_effect=_fake_completion,
        ) as gateway,
    ):
        out = await translate_presentation_fields(
            event_id=event_id,
            package_version=1,
            locale="he",
            fields={"headline": "Headline only"},
        )

    assert out["field_translation_status"]["headline"] == "generated"
    assert begin_calls
    assert begin_calls[0]["capability_key"] == "news_presentation_translate"
    assert begin_calls[0]["pipeline"] == "news_presentation_translate"
    gateway.assert_awaited()
    assert gateway.await_args.kwargs["pipeline"] == "news_presentation_translate"
