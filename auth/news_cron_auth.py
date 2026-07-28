"""Cron / internal secret for News pipeline invocation."""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status

NEWS_CRON_SECRET_HEADER = "X-BEN-News-Cron-Secret"
NEWS_CRON_SECRET_ENV = "BEN_NEWS_CRON_SECRET"


def configured_news_cron_secret() -> str:
    return os.getenv(NEWS_CRON_SECRET_ENV, "").strip()


def verify_news_cron_secret(supplied: str | None) -> bool:
    expected = configured_news_cron_secret()
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.strip(), expected)


def assert_news_cron_or_admin(request: Request, *, admin_ok: bool) -> str:
    """
    Allow either a valid cron secret header or an already-authorized news admin.
    Returns auth mode: 'cron_secret' | 'news_admin'.
    """
    if verify_news_cron_secret(request.headers.get(NEWS_CRON_SECRET_HEADER)):
        return "cron_secret"
    if admin_ok:
        return "news_admin"
    if not configured_news_cron_secret():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="News admin or cron secret required (BEN_NEWS_CRON_SECRET not configured)",
        )
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing news cron secret",
    )
