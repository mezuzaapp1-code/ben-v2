"""Syntactic feed URL validation — no DNS, no outbound HTTP."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse, urlunparse

_MAX_LEN = 2048
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
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _is_blocked_ip(host: str) -> bool | None:
    """Return True if blocked, False if public-looking literal IP, None if not an IP literal."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Bracketed IPv6 already stripped; try without zone id
        if "%" in host:
            try:
                ip = ipaddress.ip_address(host.split("%", 1)[0])
            except ValueError:
                return None
        else:
            return None

    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    ):
        return True
    # Explicit cloud metadata address (also link-local, but keep named)
    if str(ip) == "169.254.169.254":
        return True
    return False


def normalize_and_validate_feed_url(raw: str | None) -> tuple[str | None, list[str]]:
    """
    Strip, validate, and normalize a feed URL.

    Returns (normalized_url_or_None, errors).
    Does not resolve DNS or open network connections.
    """
    errors: list[str] = []
    if raw is None:
        return None, ["feed_url is required"]

    text = raw.strip()
    if not text:
        return None, ["feed_url is required"]
    if len(text) > _MAX_LEN:
        return None, [f"feed_url must be at most {_MAX_LEN} characters"]

    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        errors.append("feed_url scheme must be http or https")

    if parsed.username is not None or parsed.password is not None:
        errors.append("feed_url must not contain embedded credentials")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        errors.append("feed_url must include a hostname")
    else:
        if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
            errors.append("feed_url hostname is not allowed")
        blocked = _is_blocked_ip(host)
        if blocked is True:
            errors.append("feed_url must not target a private, loopback, link-local, or reserved address")
        elif blocked is None and _IPV4_RE.match(host):
            # Malformed dotted-quad that ip_address rejected — treat as invalid host
            errors.append("feed_url hostname is not a valid IP or DNS name")

    if errors:
        return None, errors

    # Rebuild with normalized scheme/host; preserve path, params, query; drop fragment.
    path = parsed.path or ""
    netloc = host
    if parsed.port is not None:
        default_port = 80 if scheme == "http" else 443
        if parsed.port != default_port:
            netloc = f"{host}:{parsed.port}"

    normalized = urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))
    if len(normalized) > _MAX_LEN:
        return None, [f"feed_url must be at most {_MAX_LEN} characters"]
    return normalized, []


def validate_feed_url(raw: str | None) -> dict:
    """Payload for POST /sources/validate."""
    normalized, errors = normalize_and_validate_feed_url(raw)
    return {
        "valid": not errors,
        "errors": errors,
        "normalized_url": normalized,
    }
