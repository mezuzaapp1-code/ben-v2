"""On-demand EventPackage presentation translation (headline/summary) with cache."""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from auth.config import get_anonymous_org_id
from database.connection import get_db_session
from database.models import NewsPresentationLocaleUnit
from services.inference.execution_context import begin_execution_context, set_execution_context
from services.model_gateway import accounted_openai_chat_completion
from services.ops.structured_log import log_info, log_warning

TRANSLATION_ENGINE_VERSION = "news_mt_v1"
CAPABILITY_KEY = "news_presentation_translate"
SUPPORTED_LOCALES = frozenset({"en", "he"})
V1_FIELD_KEYS = frozenset({"headline", "summary"})

TranslationStatus = Literal["cached", "generated", "fallback_en", "failed"]

_WHITESPACE_RE = re.compile(r"\s+")

_STATUS_RANK = {
    "failed": 0,
    "fallback_en": 1,
    "generated": 2,
    "cached": 3,
}


def normalize_locale(raw: str | None) -> str:
    text = (raw or "en").strip().lower().replace("_", "-")
    if text.startswith("he"):
        return "he"
    if text.startswith("en"):
        return "en"
    return "en"


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def aggregate_translation_status(
    field_statuses: dict[str, TranslationStatus],
) -> TranslationStatus | None:
    if not field_statuses:
        return None
    return min(field_statuses.values(), key=lambda s: _STATUS_RANK.get(s, 99))


def _clean_source(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


def _translate_model() -> str:
    return (
        os.getenv("BEN_NEWS_TRANSLATE_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "gpt-4o-mini"
    )


async def _lookup_cached(
    *,
    event_id: str,
    package_version: int,
    locale: str,
    field_key: str,
    text_hash: str,
) -> str | None:
    import uuid

    try:
        eid = uuid.UUID(str(event_id))
    except ValueError:
        return None
    async with get_db_session() as session:
        row = (
            await session.execute(
                select(NewsPresentationLocaleUnit).where(
                    NewsPresentationLocaleUnit.event_id == eid,
                    NewsPresentationLocaleUnit.package_version == package_version,
                    NewsPresentationLocaleUnit.locale == locale,
                    NewsPresentationLocaleUnit.field_key == field_key,
                    NewsPresentationLocaleUnit.source_text_hash == text_hash,
                    NewsPresentationLocaleUnit.translation_engine_version
                    == TRANSLATION_ENGINE_VERSION,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return row.translated_text


async def _store_cached(
    *,
    event_id: str,
    package_version: int,
    locale: str,
    field_key: str,
    text_hash: str,
    source_text: str,
    translated_text: str,
    model: str | None,
) -> None:
    import uuid

    eid = uuid.UUID(str(event_id))
    stmt = (
        pg_insert(NewsPresentationLocaleUnit)
        .values(
            event_id=eid,
            package_version=package_version,
            locale=locale,
            field_key=field_key,
            source_text_hash=text_hash,
            translation_engine_version=TRANSLATION_ENGINE_VERSION,
            source_text=source_text,
            translated_text=translated_text,
            model=model,
        )
        .on_conflict_do_nothing(
            constraint="uq_news_presentation_locale_identity",
        )
    )
    async with get_db_session() as session:
        await session.execute(stmt)
        await session.commit()


async def _call_translate(*, text: str, locale: str, field_key: str) -> str:
    target = "Hebrew" if locale == "he" else "English"
    model = _translate_model()
    tenant_id = get_anonymous_org_id()
    begin_execution_context(
        org_id=tenant_id,
        workspace_id=None,
        capability_key=CAPABILITY_KEY,
        pipeline=CAPABILITY_KEY,
        provider="openai",
        model=model,
    )
    try:
        data = await accounted_openai_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise translator for a news intelligence product. "
                        f"Translate the user text into {target}. "
                        "Preserve meaning exactly. Do not add facts, commentary, or titles. "
                        "Keep proper nouns (companies, products, people) unchanged when commonly "
                        "left in English. Return only the translated text."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Field: {field_key}\n\n{text}",
                },
            ],
            tools=None,
            tenant_id=tenant_id,
            model=model,
            pipeline=CAPABILITY_KEY,
        )
    finally:
        set_execution_context(None)

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    out = _clean_source(str(message.get("content") or ""))
    if not out:
        raise RuntimeError("empty_translation")
    return out[:8000]


async def translate_presentation_fields(
    *,
    event_id: str,
    package_version: int,
    locale: str,
    fields: dict[str, str],
) -> dict[str, Any]:
    """
    Return localized field map for V1 presentation fields.

    Output:
      texts: {field_key: text}
      locale
      fallback_fields
      field_translation_status: {field_key: cached|generated|fallback_en|failed}
      translation_status: aggregate of field statuses (None for English)
      original_locale_indicator
      translation_engine_version
    """
    loc = normalize_locale(locale)
    texts: dict[str, str] = {}
    fallback_fields: list[str] = []
    field_translation_status: dict[str, TranslationStatus] = {}

    if loc == "en":
        for key in ("headline", "summary"):
            if key in fields:
                texts[key] = _clean_source(fields[key])
        return {
            "texts": texts,
            "locale": "en",
            "fallback_fields": [],
            "field_translation_status": {},
            "translation_status": None,
            "original_locale_indicator": False,
            "translation_engine_version": TRANSLATION_ENGINE_VERSION,
        }

    for key, raw in fields.items():
        if key not in V1_FIELD_KEYS:
            continue
        source = _clean_source(raw)
        if not source:
            texts[key] = ""
            continue
        text_hash = source_text_hash(source)
        try:
            cached = await _lookup_cached(
                event_id=event_id,
                package_version=package_version,
                locale=loc,
                field_key=key,
                text_hash=text_hash,
            )
            if cached is not None:
                texts[key] = cached
                field_translation_status[key] = "cached"
                continue
            translated = await _call_translate(text=source, locale=loc, field_key=key)
            await _store_cached(
                event_id=event_id,
                package_version=package_version,
                locale=loc,
                field_key=key,
                text_hash=text_hash,
                source_text=source,
                translated_text=translated,
                model=_translate_model(),
            )
            texts[key] = translated
            field_translation_status[key] = "generated"
            log_info(
                "news presentation translated",
                subsystem="news_presentation",
                operation="translate_presentation_fields",
                outcome="ok",
                field_key=key,
                locale=loc,
            )
        except RuntimeError as exc:
            texts[key] = source
            fallback_fields.append(key)
            field_translation_status[key] = (
                "failed" if str(exc) == "empty_translation" else "fallback_en"
            )
            log_warning(
                "news presentation translation failed; using English",
                subsystem="news_presentation",
                operation="translate_presentation_fields",
                outcome="error",
                field_key=key,
                locale=loc,
                error_class=type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001
            texts[key] = source
            fallback_fields.append(key)
            field_translation_status[key] = "fallback_en"
            log_warning(
                "news presentation translation failed; using English",
                subsystem="news_presentation",
                operation="translate_presentation_fields",
                outcome="error",
                field_key=key,
                locale=loc,
                error_class=type(exc).__name__,
            )

    return {
        "texts": texts,
        "locale": loc,
        "fallback_fields": fallback_fields,
        "field_translation_status": field_translation_status,
        "translation_status": aggregate_translation_status(field_translation_status),
        "original_locale_indicator": bool(fallback_fields),
        "translation_engine_version": TRANSLATION_ENGINE_VERSION,
    }


def fields_from_package(package: dict[str, Any] | Any) -> dict[str, str]:
    if hasattr(package, "headline"):
        return {
            "headline": str(getattr(package, "headline") or ""),
            "summary": str(getattr(package, "summary") or ""),
        }
    return {
        "headline": str((package or {}).get("headline") or ""),
        "summary": str((package or {}).get("summary") or ""),
    }
