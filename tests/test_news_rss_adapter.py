"""N3.0 RssAtomAdapter parse/normalize tests."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from services.acquisition.protocols import AdapterParseError
from services.acquisition.types import AcquisitionContext, FetchResult, new_acquisition_id
from services.news.rss_adapter import RssAtomAdapter

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <link>https://example.com/</link>
    <item>
      <title>Hello &lt;b&gt;World&lt;/b&gt;</title>
      <link>/posts/hello</link>
      <guid isPermaLink="false">guid-1</guid>
      <description><![CDATA[<p>Summary text</p>]]></description>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title></title>
      <link>https://example.com/posts/two</link>
    </item>
  </channel>
</rss>
"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Example</title>
  <link href="https://example.com/"/>
  <entry>
    <title>Atom One</title>
    <id>tag:example.com,2024:1</id>
    <link href="https://example.com/a1"/>
    <updated>2024-02-01T10:00:00Z</updated>
    <summary>Atom summary</summary>
  </entry>
</feed>
"""


def _ctx() -> AcquisitionContext:
    return AcquisitionContext(
        acquisition_id=new_acquisition_id(),
        source_id=uuid.uuid4(),
        source_name="Example",
        feed_url="https://example.com/feed.xml",
        category="tech",
        language="en",
        enabled=True,
        started_at=datetime.now(timezone.utc),
    )


def _fetch(body: bytes, ok: bool = True) -> FetchResult:
    aid = new_acquisition_id()
    return FetchResult(
        acquisition_id=aid,
        ok=ok,
        requested_url="https://example.com/feed.xml",
        final_url="https://example.com/feed.xml",
        status_code=200 if ok else 500,
        content_type="application/rss+xml",
        body=body if ok else None,
        body_size=len(body) if ok else 0,
    )


def test_parse_rss_normalizes_items():
    adapter = RssAtomAdapter()
    assert adapter.name == "rss_atom"
    ctx = _ctx()
    fetch = _fetch(RSS)
    # align acquisition ids
    fetch = FetchResult(
        acquisition_id=ctx.acquisition_id,
        ok=True,
        requested_url=fetch.requested_url,
        final_url=fetch.final_url,
        status_code=200,
        content_type="application/rss+xml",
        body=RSS,
        body_size=len(RSS),
    )
    items = adapter.parse(ctx, fetch)
    assert len(items) == 2
    first = items[0]
    assert first.guid == "guid-1"
    assert first.guid_source == "feed_guid"
    assert first.canonical_url == "https://example.com/posts/hello"
    assert first.title == "Hello World"
    assert first.summary == "Summary text"
    assert first.category == "tech"
    assert first.published_at is not None
    assert first.published_at.tzinfo is not None

    second = items[1]
    assert second.title == "(untitled)"
    assert second.guid_source in ("link", "feed_guid")
    assert second.canonical_url == "https://example.com/posts/two"


def test_parse_atom():
    adapter = RssAtomAdapter()
    ctx = _ctx()
    fetch = FetchResult(
        acquisition_id=ctx.acquisition_id,
        ok=True,
        requested_url=ctx.feed_url,
        final_url=ctx.feed_url,
        status_code=200,
        content_type="application/atom+xml",
        body=ATOM,
        body_size=len(ATOM),
    )
    items = adapter.parse(ctx, fetch)
    assert len(items) == 1
    assert items[0].title == "Atom One"
    assert items[0].guid == "tag:example.com,2024:1"
    assert items[0].canonical_url == "https://example.com/a1"


def test_parse_requires_successful_fetch():
    adapter = RssAtomAdapter()
    ctx = _ctx()
    fetch = _fetch(b"", ok=False)
    fetch = FetchResult(
        acquisition_id=ctx.acquisition_id,
        ok=False,
        requested_url=ctx.feed_url,
        final_url=None,
        status_code=500,
        content_type=None,
        body=None,
        body_size=0,
    )
    with pytest.raises(AdapterParseError) as ei:
        adapter.parse(ctx, fetch)
    assert ei.value.error.error_class == "parse_error"


def test_malformed_feed_with_no_entries():
    adapter = RssAtomAdapter()
    ctx = _ctx()
    body = b"this is not xml at all !!!!"
    fetch = FetchResult(
        acquisition_id=ctx.acquisition_id,
        ok=True,
        requested_url=ctx.feed_url,
        final_url=ctx.feed_url,
        status_code=200,
        content_type="text/plain",
        body=body,
        body_size=len(body),
    )
    with pytest.raises(AdapterParseError) as ei:
        adapter.parse(ctx, fetch)
    assert ei.value.error.error_class == "parse_error"


def test_guid_fallback_to_hash_when_no_link():
    adapter = RssAtomAdapter()
    ctx = _ctx()
    body = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Only Title</title></item>
    </channel></rss>"""
    fetch = FetchResult(
        acquisition_id=ctx.acquisition_id,
        ok=True,
        requested_url=ctx.feed_url,
        final_url=ctx.feed_url,
        status_code=200,
        content_type="application/rss+xml",
        body=body,
        body_size=len(body),
    )
    items = adapter.parse(ctx, fetch)
    assert len(items) == 1
    assert items[0].guid_source == "hash"
    assert len(items[0].guid) == 64
