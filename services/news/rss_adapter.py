"""RSS/Atom SourceAdapter — parse + normalize only (no network, no DB)."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser

from services.acquisition.protocols import AdapterParseError
from services.acquisition.types import (
    ACQUISITION_SUMMARY_MAX_CHARS,
    AcquisitionContext,
    FetchResult,
    GuidSource,
    NormalizedItem,
    make_error,
)

_TAG_RE = re.compile(r"<[^>]+>")


class RssAtomAdapter:
    @property
    def name(self) -> str:
        return "rss_atom"

    def parse(self, ctx: AcquisitionContext, fetch: FetchResult) -> list[NormalizedItem]:
        if not fetch.ok or fetch.body is None:
            raise AdapterParseError(
                make_error(
                    ctx.acquisition_id,
                    stage="parse",
                    error_class="parse_error",
                    message="cannot parse: fetch did not succeed",
                )
            )

        try:
            parsed = feedparser.parse(fetch.body)
        except Exception as exc:  # noqa: BLE001
            raise AdapterParseError(
                make_error(
                    ctx.acquisition_id,
                    stage="parse",
                    error_class="parse_error",
                    message=f"feedparser failed: {exc}",
                )
            ) from exc

        bozo = getattr(parsed, "bozo", 0)
        entries = list(getattr(parsed, "entries", []) or [])
        if bozo and not entries:
            bozo_exc = getattr(parsed, "bozo_exception", None)
            msg = f"malformed feed: {bozo_exc}" if bozo_exc else "malformed feed with no entries"
            raise AdapterParseError(
                make_error(
                    ctx.acquisition_id,
                    stage="parse",
                    error_class="parse_error",
                    message=msg[:500],
                )
            )

        base = fetch.final_url or ctx.feed_url
        items: list[NormalizedItem] = []
        for entry in entries:
            try:
                item = _normalize_entry(ctx, entry, base_url=base)
            except AdapterParseError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AdapterParseError(
                    make_error(
                        ctx.acquisition_id,
                        stage="normalize",
                        error_class="normalize_error",
                        message=f"failed to normalize entry: {exc}",
                    )
                ) from exc
            if item is not None:
                items.append(item)
        return items


def _strip_html(text: str) -> str:
    return _TAG_RE.sub(" ", text).replace("&nbsp;", " ")


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _absolute_url(raw: str | None, base_url: str) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    abs_url = urljoin(base_url, value)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return abs_url[:2048]


def _entry_link(entry: Any) -> str | None:
    link = getattr(entry, "link", None) or (entry.get("link") if isinstance(entry, dict) else None)
    if link:
        return str(link)
    links = getattr(entry, "links", None) or []
    for item in links:
        href = item.get("href") if isinstance(item, dict) else getattr(item, "href", None)
        rel = item.get("rel") if isinstance(item, dict) else getattr(item, "rel", None)
        if href and (rel in (None, "alternate", "self") or not rel):
            return str(href)
    return None


def _entry_guid(entry: Any) -> str | None:
    for key in ("id", "guid"):
        val = getattr(entry, key, None)
        if val is None and isinstance(entry, dict):
            val = entry.get(key)
        if val:
            text = str(val).strip()
            if text:
                return text[:1024]
    return None


def _entry_title(entry: Any) -> str:
    title = getattr(entry, "title", None)
    if title is None and isinstance(entry, dict):
        title = entry.get("title")
    text = _collapse_ws(_strip_html(str(title))) if title else ""
    return (text[:1024] if text else "(untitled)")


def _entry_summary(entry: Any) -> str | None:
    for key in ("summary", "description"):
        val = getattr(entry, key, None)
        if val is None and isinstance(entry, dict):
            val = entry.get(key)
        if val:
            text = _collapse_ws(_strip_html(str(val)))
            if text:
                return text[:ACQUISITION_SUMMARY_MAX_CHARS]
    content = getattr(entry, "content", None) or []
    if content:
        first = content[0]
        val = first.get("value") if isinstance(first, dict) else getattr(first, "value", None)
        if val:
            text = _collapse_ws(_strip_html(str(val)))
            if text:
                return text[:ACQUISITION_SUMMARY_MAX_CHARS]
    return None


def _struct_time_to_dt(value: struct_time | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime(*value[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _entry_published(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, key, None)
        dt = _struct_time_to_dt(parsed)
        if dt:
            return dt
    for key in ("published", "updated"):
        raw = getattr(entry, key, None)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError):
            continue
    return None


def _entry_image(entry: Any, base_url: str) -> str | None:
    media = getattr(entry, "media_thumbnail", None) or []
    if media:
        url = media[0].get("url") if isinstance(media[0], dict) else None
        abs_url = _absolute_url(url, base_url)
        if abs_url:
            return abs_url
    for enc in getattr(entry, "enclosures", None) or []:
        href = enc.get("href") if isinstance(enc, dict) else getattr(enc, "href", None)
        typ = enc.get("type") if isinstance(enc, dict) else getattr(enc, "type", None)
        if href and (not typ or str(typ).startswith("image/")):
            abs_url = _absolute_url(str(href), base_url)
            if abs_url:
                return abs_url
    summary = getattr(entry, "summary", None) or ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', str(summary), re.I)
    if match:
        return _absolute_url(match.group(1), base_url)
    return None


def _normalize_entry(
    ctx: AcquisitionContext, entry: Any, *, base_url: str
) -> NormalizedItem | None:
    link_raw = _entry_link(entry)
    canonical = _absolute_url(link_raw, base_url)
    title = _entry_title(entry)

    guid_raw = _entry_guid(entry)
    guid_source: GuidSource
    if guid_raw:
        guid = guid_raw[:1024]
        # If guid looks like a relative URL, absolutize when possible
        if "://" not in guid and guid.startswith("/"):
            abs_guid = _absolute_url(guid, base_url)
            guid = (abs_guid or guid)[:1024]
        guid_source = "feed_guid"
    elif canonical:
        guid = canonical[:1024]
        guid_source = "link"
    else:
        # Need some stable identity; require at least title
        seed = f"{ctx.source_id}|{canonical or ''}|{title}"
        guid = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:64]
        guid_source = "hash"
        # Without a URL, invent a placeholder canonical pointing at feed (still http/https)
        canonical = (base_url or ctx.feed_url)[:2048]

    if not canonical:
        return None

    summary = _entry_summary(entry)
    image_url = _entry_image(entry, base_url)
    published_at = _entry_published(entry)

    return NormalizedItem(
        acquisition_id=ctx.acquisition_id,
        source_id=ctx.source_id,
        guid=guid,
        canonical_url=canonical[:2048],
        title=title[:1024],
        category=(ctx.category or "general")[:64],
        summary=summary,
        image_url=image_url[:2048] if image_url else None,
        published_at=published_at,
        guid_source=guid_source,
    )
