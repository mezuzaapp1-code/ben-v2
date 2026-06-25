"""In-process rate limiter for public Basalt API endpoints."""
from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


@dataclass
class BasaltRateLimiter:
    max_requests: int = field(default_factory=lambda: _int_env("BASALT_PUBLIC_RATE_LIMIT", 60))
    window_seconds: int = field(default_factory=lambda: _int_env("BASALT_PUBLIC_RATE_WINDOW_S", 60))
    _buckets: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list), repr=False)

    def _client_key(self, request: Request, endpoint: str) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        client = forwarded or (request.client.host if request.client else "unknown")
        return f"{endpoint}:{client}"

    def check(self, request: Request, endpoint: str) -> None:
        key = self._client_key(request, endpoint)
        now = time.monotonic()
        window = self.window_seconds
        hits = [t for t in self._buckets[key] if now - t < window]
        if len(hits) >= self.max_requests:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded for public Basalt API",
                headers={"Retry-After": str(window)},
            )
        hits.append(now)
        self._buckets[key] = hits


_limiter = BasaltRateLimiter()


def enforce_basalt_rate_limit(request: Request, endpoint: str) -> None:
    _limiter.check(request, endpoint)
