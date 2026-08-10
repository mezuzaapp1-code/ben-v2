"""Cron / internal secret for the document-processing drain endpoint (Gate 3B).

The drain is a system path (it claims jobs cross-org via the Gate 3A SECURITY
DEFINER functions), so it is gated by a dedicated shared secret and never exposed
to ordinary product sessions. Fail-closed: if the secret is not configured, the
endpoint is unavailable.
"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status

DOC_PROCESSING_CRON_SECRET_HEADER = "X-BEN-Doc-Processing-Cron-Secret"
DOC_PROCESSING_CRON_SECRET_ENV = "BEN_DOC_PROCESSING_CRON_SECRET"


def configured_doc_processing_cron_secret() -> str:
    return os.getenv(DOC_PROCESSING_CRON_SECRET_ENV, "").strip()


def verify_doc_processing_cron_secret(supplied: str | None) -> bool:
    expected = configured_doc_processing_cron_secret()
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.strip(), expected)


def assert_doc_processing_cron(request: Request) -> str:
    """Require a valid cron secret. Returns auth mode 'cron_secret'."""
    if verify_doc_processing_cron_secret(request.headers.get(DOC_PROCESSING_CRON_SECRET_HEADER)):
        return "cron_secret"
    if not configured_doc_processing_cron_secret():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document processing drain disabled (BEN_DOC_PROCESSING_CRON_SECRET not configured)",
        )
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing document-processing cron secret",
    )
