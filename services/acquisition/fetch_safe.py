"""SSRF-safe bounded HTTP GET for acquisition (N3.0)."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx

from services.acquisition.types import (
    ACQUISITION_CONNECT_TIMEOUT_S,
    ACQUISITION_MAX_BODY_BYTES,
    ACQUISITION_MAX_REDIRECTS,
    ACQUISITION_TOTAL_TIMEOUT_S,
    ACQUISITION_USER_AGENT,
    AcquisitionContext,
    AcquisitionError,
    FetchResult,
    make_error,
)
from services.news.feed_url import normalize_and_validate_feed_url
from services.ops.structured_log import log_info, log_warning

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)

_ALLOWED_CONTENT_HINTS = (
    "xml",
    "rss",
    "atom",
    "text/html",
    "application/octet-stream",
)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or str(ip) == "169.254.169.254"
    )


def _validate_url_syntax(url: str, acquisition_id: str) -> tuple[str | None, AcquisitionError | None]:
    normalized, errors = normalize_and_validate_feed_url(url)
    if errors or not normalized:
        return None, make_error(
            acquisition_id,
            stage="fetch",
            error_class="invalid_feed_url",
            message="; ".join(errors) if errors else "invalid feed URL",
            details={"reason": "syntax"},
        )
    return normalized, None


async def _resolve_and_validate_host(
    hostname: str, acquisition_id: str
) -> AcquisitionError | None:
    host = (hostname or "").strip().lower()
    if not host:
        return make_error(
            acquisition_id,
            stage="fetch",
            error_class="ssrf_blocked",
            message="missing hostname",
        )
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        return make_error(
            acquisition_id,
            stage="fetch",
            error_class="ssrf_blocked",
            message="hostname is not allowed",
            details={"hostname": host[:256]},
        )

    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            return make_error(
                acquisition_id,
                stage="fetch",
                error_class="ssrf_blocked",
                message="target address is not allowed",
                details={"hostname": host[:256]},
            )
        return None
    except ValueError:
        pass

    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        return make_error(
            acquisition_id,
            stage="fetch",
            error_class="dns_blocked",
            message=f"DNS resolution failed: {exc}",
            details={"hostname": host[:256]},
        )

    if not infos:
        return make_error(
            acquisition_id,
            stage="fetch",
            error_class="dns_blocked",
            message="DNS returned no addresses",
            details={"hostname": host[:256]},
        )

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            return make_error(
                acquisition_id,
                stage="fetch",
                error_class="dns_blocked",
                message="resolved address is not allowed",
                details={"hostname": host[:256]},
            )
    return None


def _content_type_allowed(content_type: str | None) -> bool:
    if not content_type:
        return True
    ct = content_type.lower()
    return any(h in ct for h in _ALLOWED_CONTENT_HINTS)


def _fail(
    *,
    acquisition_id: str,
    requested: str,
    final_url: str | None,
    status_code: int | None,
    content_type: str | None,
    body_size: int,
    redirect_count: int,
    started: float,
    error: AcquisitionError,
) -> FetchResult:
    return FetchResult(
        acquisition_id=acquisition_id,
        ok=False,
        requested_url=requested,
        final_url=final_url,
        status_code=status_code,
        content_type=content_type,
        body=None,
        body_size=body_size,
        redirect_count=redirect_count,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        error=error,
    )


async def fetch_safe(ctx: AcquisitionContext) -> FetchResult:
    """Bounded GET with SSRF checks on request URL and every redirect hop."""
    acquisition_id = ctx.acquisition_id
    started = time.perf_counter()
    requested = ctx.feed_url

    url, err = _validate_url_syntax(requested, acquisition_id)
    if err or not url:
        return _fail(
            acquisition_id=acquisition_id,
            requested=requested,
            final_url=None,
            status_code=None,
            content_type=None,
            body_size=0,
            redirect_count=0,
            started=started,
            error=err or make_error(
                acquisition_id,
                stage="fetch",
                error_class="invalid_feed_url",
                message="invalid feed URL",
            ),
        )

    current = url
    redirect_count = 0
    timeout = httpx.Timeout(ACQUISITION_TOTAL_TIMEOUT_S, connect=ACQUISITION_CONNECT_TIMEOUT_S)
    headers = {
        "User-Agent": ctx.user_agent or ACQUISITION_USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            verify=True,
            trust_env=False,
        ) as client:
            while True:
                parsed = urlparse(current)
                host_err = await _resolve_and_validate_host(parsed.hostname or "", acquisition_id)
                if host_err:
                    return _fail(
                        acquisition_id=acquisition_id,
                        requested=requested,
                        final_url=current,
                        status_code=None,
                        content_type=None,
                        body_size=0,
                        redirect_count=redirect_count,
                        started=started,
                        error=host_err,
                    )

                try:
                    async with client.stream("GET", current, headers=headers) as resp:
                        if resp.status_code in (301, 302, 303, 307, 308):
                            redirect_count += 1
                            if redirect_count > ACQUISITION_MAX_REDIRECTS:
                                return _fail(
                                    acquisition_id=acquisition_id,
                                    requested=requested,
                                    final_url=current,
                                    status_code=resp.status_code,
                                    content_type=None,
                                    body_size=0,
                                    redirect_count=redirect_count,
                                    started=started,
                                    error=make_error(
                                        acquisition_id,
                                        stage="fetch",
                                        error_class="redirect_blocked",
                                        message="too many redirects",
                                        http_status=resp.status_code,
                                        details={"redirect_count": redirect_count},
                                    ),
                                )
                            location = resp.headers.get("Location")
                            if not location:
                                return _fail(
                                    acquisition_id=acquisition_id,
                                    requested=requested,
                                    final_url=current,
                                    status_code=resp.status_code,
                                    content_type=None,
                                    body_size=0,
                                    redirect_count=redirect_count,
                                    started=started,
                                    error=make_error(
                                        acquisition_id,
                                        stage="fetch",
                                        error_class="redirect_blocked",
                                        message="redirect missing Location",
                                        http_status=resp.status_code,
                                    ),
                                )
                            next_url = urljoin(current, location)
                            next_norm, next_err = _validate_url_syntax(next_url, acquisition_id)
                            if next_err or not next_norm:
                                return _fail(
                                    acquisition_id=acquisition_id,
                                    requested=requested,
                                    final_url=current,
                                    status_code=resp.status_code,
                                    content_type=None,
                                    body_size=0,
                                    redirect_count=redirect_count,
                                    started=started,
                                    error=make_error(
                                        acquisition_id,
                                        stage="fetch",
                                        error_class="redirect_blocked",
                                        message="redirect target failed URL validation",
                                        http_status=resp.status_code,
                                        details={"reason": "invalid_redirect_target"},
                                    ),
                                )
                            prev_scheme = (urlparse(current).scheme or "").lower()
                            next_scheme = (urlparse(next_norm).scheme or "").lower()
                            if prev_scheme == "https" and next_scheme == "http":
                                return _fail(
                                    acquisition_id=acquisition_id,
                                    requested=requested,
                                    final_url=current,
                                    status_code=resp.status_code,
                                    content_type=None,
                                    body_size=0,
                                    redirect_count=redirect_count,
                                    started=started,
                                    error=make_error(
                                        acquisition_id,
                                        stage="fetch",
                                        error_class="redirect_blocked",
                                        message="HTTPS to HTTP redirect downgrade is not allowed",
                                        http_status=resp.status_code,
                                        details={"reason": "https_downgrade"},
                                    ),
                                )
                            await resp.aclose()
                            current = next_norm
                            continue

                        content_type = resp.headers.get("Content-Type")
                        if content_type and len(content_type) > 256:
                            content_type = content_type[:256]

                        if resp.status_code != 200:
                            retryable = resp.status_code == 429 or resp.status_code >= 500
                            return _fail(
                                acquisition_id=acquisition_id,
                                requested=requested,
                                final_url=str(resp.url),
                                status_code=resp.status_code,
                                content_type=content_type,
                                body_size=0,
                                redirect_count=redirect_count,
                                started=started,
                                error=make_error(
                                    acquisition_id,
                                    stage="fetch",
                                    error_class="http_error",
                                    message=f"unexpected HTTP status {resp.status_code}",
                                    retryable=retryable,
                                    http_status=resp.status_code,
                                    details={"status_code": resp.status_code},
                                ),
                            )

                        if not _content_type_allowed(content_type):
                            return _fail(
                                acquisition_id=acquisition_id,
                                requested=requested,
                                final_url=str(resp.url),
                                status_code=resp.status_code,
                                content_type=content_type,
                                body_size=0,
                                redirect_count=redirect_count,
                                started=started,
                                error=make_error(
                                    acquisition_id,
                                    stage="fetch",
                                    error_class="unsupported_content_type",
                                    message="response content-type is not allowed",
                                    http_status=resp.status_code,
                                    details={
                                        "content_type": (content_type or "")[:256],
                                        "status_code": resp.status_code,
                                    },
                                ),
                            )

                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in resp.aiter_bytes():
                            total += len(chunk)
                            if total > ACQUISITION_MAX_BODY_BYTES:
                                return _fail(
                                    acquisition_id=acquisition_id,
                                    requested=requested,
                                    final_url=str(resp.url),
                                    status_code=resp.status_code,
                                    content_type=content_type,
                                    body_size=total,
                                    redirect_count=redirect_count,
                                    started=started,
                                    error=make_error(
                                        acquisition_id,
                                        stage="fetch",
                                        error_class="response_too_large",
                                        message="feed response exceeds size limit",
                                        http_status=resp.status_code,
                                        details={"body_size": total},
                                    ),
                                )
                            chunks.append(chunk)

                        body = b"".join(chunks)
                        etag = resp.headers.get("ETag")
                        if etag and len(etag) > 1024:
                            etag = etag[:1024]
                        last_modified = resp.headers.get("Last-Modified")
                        if last_modified and len(last_modified) > 256:
                            last_modified = last_modified[:256]

                        elapsed_ms = int((time.perf_counter() - started) * 1000)
                        log_info(
                            "acquisition fetch ok",
                            subsystem="news_collector",
                            category="collect",
                            acquisition_id=acquisition_id,
                            source_id=str(ctx.source_id),
                            stage="fetch",
                            outcome="ok",
                            body_size=len(body),
                            redirect_count=redirect_count,
                            elapsed_ms=elapsed_ms,
                        )
                        return FetchResult(
                            acquisition_id=acquisition_id,
                            ok=True,
                            requested_url=requested,
                            final_url=str(resp.url),
                            status_code=200,
                            content_type=content_type,
                            body=body,
                            body_size=len(body),
                            redirect_count=redirect_count,
                            elapsed_ms=elapsed_ms,
                            etag=etag,
                            last_modified=last_modified,
                        )
                except httpx.TimeoutException:
                    return _fail(
                        acquisition_id=acquisition_id,
                        requested=requested,
                        final_url=current,
                        status_code=None,
                        content_type=None,
                        body_size=0,
                        redirect_count=redirect_count,
                        started=started,
                        error=make_error(
                            acquisition_id,
                            stage="fetch",
                            error_class="timeout",
                            message="feed fetch timed out",
                            retryable=True,
                        ),
                    )
                except httpx.HTTPError as exc:
                    return _fail(
                        acquisition_id=acquisition_id,
                        requested=requested,
                        final_url=current,
                        status_code=None,
                        content_type=None,
                        body_size=0,
                        redirect_count=redirect_count,
                        started=started,
                        error=make_error(
                            acquisition_id,
                            stage="fetch",
                            error_class="http_error",
                            message=f"HTTP client error: {exc}",
                            retryable=True,
                        ),
                    )
    except Exception as exc:  # noqa: BLE001
        log_warning(
            "acquisition fetch internal error",
            subsystem="news_collector",
            category="internal_error",
            acquisition_id=acquisition_id,
            source_id=str(ctx.source_id),
            stage="fetch",
            outcome="error",
        )
        return _fail(
            acquisition_id=acquisition_id,
            requested=requested,
            final_url=None,
            status_code=None,
            content_type=None,
            body_size=0,
            redirect_count=0,
            started=started,
            error=make_error(
                acquisition_id,
                stage="fetch",
                error_class="internal_error",
                message=f"unexpected fetch error: {exc}",
            ),
        )
