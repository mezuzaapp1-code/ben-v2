"""N3.0 fetch_safe SSRF / size / redirect tests (no real network)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from services.acquisition.fetch_safe import fetch_safe
from services.acquisition.types import (
    ACQUISITION_MAX_BODY_BYTES,
    AcquisitionContext,
    new_acquisition_id,
)


def _ctx(url: str = "https://example.com/feed.xml") -> AcquisitionContext:
    return AcquisitionContext(
        acquisition_id=new_acquisition_id(),
        source_id=uuid.uuid4(),
        source_name="Example",
        feed_url=url,
        category="tech",
        language="en",
        enabled=True,
        started_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_rejects_localhost_url():
    result = await fetch_safe(_ctx("https://localhost/feed"))
    assert result.ok is False
    assert result.error is not None
    assert result.error.error_class in ("invalid_feed_url", "ssrf_blocked")


@pytest.mark.asyncio
async def test_rejects_private_ip_literal():
    result = await fetch_safe(_ctx("https://10.0.0.5/feed"))
    assert result.ok is False
    assert result.error is not None
    assert result.error.error_class in ("invalid_feed_url", "ssrf_blocked")


@pytest.mark.asyncio
async def test_rejects_metadata_ip():
    result = await fetch_safe(_ctx("https://169.254.169.254/latest/meta-data"))
    assert result.ok is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_dns_blocked_private_resolution():
    ctx = _ctx("https://evil.example.com/feed")

    async def fake_resolve(hostname, acquisition_id):
        from services.acquisition.types import make_error

        return make_error(
            acquisition_id,
            stage="fetch",
            error_class="dns_blocked",
            message="resolved address is not allowed",
            details={"hostname": hostname},
        )

    with patch(
        "services.acquisition.fetch_safe._resolve_and_validate_host",
        side_effect=fake_resolve,
    ):
        result = await fetch_safe(ctx)
    assert result.ok is False
    assert result.error.error_class == "dns_blocked"


@pytest.mark.asyncio
async def test_redirect_to_private_blocked():
    ctx = _ctx("https://example.com/feed")

    class FakeResp:
        status_code = 302
        headers = {"Location": "https://127.0.0.1/secret"}
        url = "https://example.com/feed"

        async def aclose(self):
            return None

        async def aiter_bytes(self):
            if False:
                yield b""

    class FakeStream:
        async def __aenter__(self):
            return FakeResp()

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return FakeStream()

    async def allow_host(hostname, acquisition_id):
        if hostname in ("127.0.0.1",):
            from services.acquisition.types import make_error

            return make_error(
                acquisition_id,
                stage="fetch",
                error_class="ssrf_blocked",
                message="blocked",
                details={"hostname": hostname},
            )
        return None

    with patch("services.acquisition.fetch_safe.httpx.AsyncClient", FakeClient), patch(
        "services.acquisition.fetch_safe._resolve_and_validate_host",
        side_effect=allow_host,
    ):
        result = await fetch_safe(ctx)
    assert result.ok is False
    assert result.error is not None
    assert result.error.error_class in ("redirect_blocked", "ssrf_blocked", "invalid_feed_url")


@pytest.mark.asyncio
async def test_response_too_large():
    ctx = _ctx("https://example.com/feed")

    class FakeResp:
        status_code = 200
        headers = {"Content-Type": "application/rss+xml"}
        url = "https://example.com/feed"

        async def aclose(self):
            return None

        async def aiter_bytes(self):
            # yield chunks that exceed the cap
            chunk = b"x" * (1024 * 1024)
            sent = 0
            while sent <= ACQUISITION_MAX_BODY_BYTES:
                sent += len(chunk)
                yield chunk

    class FakeStream:
        async def __aenter__(self):
            return FakeResp()

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return FakeStream()

    async def allow_host(hostname, acquisition_id):
        return None

    with patch("services.acquisition.fetch_safe.httpx.AsyncClient", FakeClient), patch(
        "services.acquisition.fetch_safe._resolve_and_validate_host",
        side_effect=allow_host,
    ):
        result = await fetch_safe(ctx)
    assert result.ok is False
    assert result.error.error_class == "response_too_large"


@pytest.mark.asyncio
async def test_blocks_https_to_http_downgrade():
    ctx = _ctx("https://example.com/feed")

    class FakeResp:
        status_code = 302
        headers = {"Location": "http://example.com/feed"}
        url = "https://example.com/feed"

        async def aclose(self):
            return None

        async def aiter_bytes(self):
            if False:
                yield b""

    class FakeStream:
        async def __aenter__(self):
            return FakeResp()

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return FakeStream()

    async def allow_host(hostname, acquisition_id):
        return None

    with patch("services.acquisition.fetch_safe.httpx.AsyncClient", FakeClient), patch(
        "services.acquisition.fetch_safe._resolve_and_validate_host",
        side_effect=allow_host,
    ):
        result = await fetch_safe(ctx)
    assert result.ok is False
    assert result.error is not None
    assert result.error.error_class == "redirect_blocked"
    assert result.error.details and result.error.details.get("reason") == "https_downgrade"


@pytest.mark.asyncio
async def test_fetch_ok_small_body():
    ctx = _ctx("https://example.com/feed")
    body = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'

    class FakeResp:
        status_code = 200
        headers = {"Content-Type": "application/rss+xml", "ETag": '"abc"'}
        url = "https://example.com/feed"

        async def aclose(self):
            return None

        async def aiter_bytes(self):
            yield body

    class FakeStream:
        async def __aenter__(self):
            return FakeResp()

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return FakeStream()

    async def allow_host(hostname, acquisition_id):
        return None

    with patch("services.acquisition.fetch_safe.httpx.AsyncClient", FakeClient), patch(
        "services.acquisition.fetch_safe._resolve_and_validate_host",
        side_effect=allow_host,
    ):
        result = await fetch_safe(ctx)
    assert result.ok is True
    assert result.body == body
    assert result.status_code == 200
    assert result.etag == '"abc"'
