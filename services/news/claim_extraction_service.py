"""Persist and serve E1 NewsClaims — operator path only; no Event creation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update

from database.connection import get_db_session
from database.models import NewsArticle, NewsClaim, NewsClaimExtraction
from services.news.claim_contract import (
    EXTRACTOR_VERSION,
    claim_fingerprint,
    content_fingerprint,
)
from services.news.claim_extractor import LlmExtractFn, extract_claims
from services.ops.request_context import attach_request_id

DEFAULT_LIMIT = 100


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _claim_to_dict(row: NewsClaim) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "article_id": str(row.article_id),
        "text": row.claim_text,
        "claim_type": row.claim_type,
        "role": row.role,
        "source_field": row.source_field,
        "source_excerpt": row.source_excerpt,
        "source_start": row.source_start,
        "source_end": row.source_end,
        "attribution": row.attribution,
        "uncertainty": row.uncertainty,
        "status": row.status,
        "extractor_version": row.extractor_version,
        "provider": row.provider,
        "model": row.model,
        "claim_fingerprint": row.claim_fingerprint,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _extraction_to_dict(row: NewsClaimExtraction) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "article_id": str(row.article_id),
        "extractor_version": row.extractor_version,
        "content_fingerprint": row.content_fingerprint,
        "status": row.status,
        "provider": row.provider,
        "model": row.model,
        "claim_count": row.claim_count,
        "error_class": row.error_class,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


async def extract_article_claims(
    article_id: uuid.UUID,
    *,
    extractor_version: str = EXTRACTOR_VERSION,
    llm_extract_fn: LlmExtractFn | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Idempotent extraction for one article. Never mutates NewsArticle rows."""
    try:
        async with get_db_session() as session:
            article = await session.get(NewsArticle, article_id)
            if article is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Article not found")

            # Snapshot immutable article fields (do not write back).
            title = article.title
            summary = article.summary
            fp = content_fingerprint(title=title, summary=summary)

            existing_q = await session.execute(
                select(NewsClaimExtraction).where(
                    NewsClaimExtraction.article_id == article_id,
                    NewsClaimExtraction.extractor_version == extractor_version,
                )
            )
            run = existing_q.scalar_one_or_none()

            if (
                run is not None
                and run.status == "succeeded"
                and run.content_fingerprint == fp
                and not force
            ):
                claims = await _load_claims(
                    session, article_id=article_id, extractor_version=extractor_version
                )
                return attach_request_id(
                    {
                        "idempotent": True,
                        "extraction": _extraction_to_dict(run),
                        "claims": [_claim_to_dict(c) for c in claims],
                    }
                )

            try:
                result = await extract_claims(
                    title=title,
                    summary=summary,
                    llm_extract_fn=llm_extract_fn,
                )
            except ValueError as exc:
                err = str(exc)
                error_class = (
                    "malformed_model_output"
                    if "malformed_model_output" in err
                    else "extraction_error"
                )
                run = await _upsert_run(
                    session,
                    run=run,
                    article_id=article_id,
                    extractor_version=extractor_version,
                    content_fingerprint=fp,
                    status="failed",
                    provider=None,
                    model=None,
                    claim_count=0,
                    error_class=error_class,
                    error_message=err[:2000],
                )
                await session.commit()
                return attach_request_id(
                    {
                        "idempotent": False,
                        "extraction": _extraction_to_dict(run),
                        "claims": [],
                    }
                )

            # Supersede claims from other extractor versions for this article.
            await session.execute(
                update(NewsClaim)
                .where(
                    NewsClaim.article_id == article_id,
                    NewsClaim.extractor_version != extractor_version,
                    NewsClaim.status == "extracted",
                )
                .values(status="superseded")
            )

            # Replace claims for this version (safe re-run after failure / force).
            old_claims = (
                await session.execute(
                    select(NewsClaim).where(
                        NewsClaim.article_id == article_id,
                        NewsClaim.extractor_version == extractor_version,
                    )
                )
            ).scalars().all()
            for row in old_claims:
                await session.delete(row)
            await session.flush()

            persisted: list[NewsClaim] = []
            for c in result.claims:
                fp_claim = claim_fingerprint(
                    text=c.text,
                    claim_type=c.claim_type,
                    source_field=c.source_field,
                    source_start=c.source_start,
                    source_end=c.source_end,
                )
                row = NewsClaim(
                    article_id=article_id,
                    claim_fingerprint=fp_claim,
                    claim_text=c.text,
                    claim_type=c.claim_type,
                    role=c.role,
                    source_field=c.source_field,
                    source_excerpt=c.source_excerpt,
                    source_start=c.source_start,
                    source_end=c.source_end,
                    attribution=c.attribution,
                    uncertainty=c.uncertainty,
                    status="extracted",
                    extractor_version=extractor_version,
                    provider=result.provider,
                    model=result.model,
                )
                session.add(row)
                persisted.append(row)

            run = await _upsert_run(
                session,
                run=run,
                article_id=article_id,
                extractor_version=extractor_version,
                content_fingerprint=fp,
                status="succeeded",
                provider=result.provider,
                model=result.model,
                claim_count=len(persisted),
                error_class=None,
                error_message=None,
            )
            await session.commit()
            for row in persisted:
                await session.refresh(row)
            await session.refresh(run)

            return attach_request_id(
                {
                    "idempotent": False,
                    "extraction": _extraction_to_dict(run),
                    "claims": [_claim_to_dict(c) for c in persisted],
                }
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to extract claims",
        ) from exc


async def list_article_claims(
    article_id: uuid.UUID,
    *,
    extractor_version: str | None = None,
    include_superseded: bool = False,
) -> dict[str, Any]:
    try:
        async with get_db_session() as session:
            article = await session.get(NewsArticle, article_id)
            if article is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Article not found")
            version = extractor_version or EXTRACTOR_VERSION
            claims = await _load_claims(
                session,
                article_id=article_id,
                extractor_version=version,
                include_superseded=include_superseded,
            )
            return attach_request_id(
                {
                    "article_id": str(article_id),
                    "extractor_version": version,
                    "items": [_claim_to_dict(c) for c in claims],
                    "count": len(claims),
                }
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list claims",
        ) from exc


async def get_article_extraction(
    article_id: uuid.UUID,
    *,
    extractor_version: str = EXTRACTOR_VERSION,
) -> dict[str, Any]:
    try:
        async with get_db_session() as session:
            article = await session.get(NewsArticle, article_id)
            if article is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Article not found")
            run = (
                await session.execute(
                    select(NewsClaimExtraction).where(
                        NewsClaimExtraction.article_id == article_id,
                        NewsClaimExtraction.extractor_version == extractor_version,
                    )
                )
            ).scalar_one_or_none()
            if run is None:
                return attach_request_id(
                    {
                        "article_id": str(article_id),
                        "extractor_version": extractor_version,
                        "extraction": None,
                        "status": "not_started",
                    }
                )
            return attach_request_id(
                {
                    "article_id": str(article_id),
                    "extractor_version": extractor_version,
                    "extraction": _extraction_to_dict(run),
                    "status": run.status,
                }
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load extraction status",
        ) from exc


async def _load_claims(
    session,
    *,
    article_id: uuid.UUID,
    extractor_version: str,
    include_superseded: bool = False,
) -> list[NewsClaim]:
    q = select(NewsClaim).where(
        NewsClaim.article_id == article_id,
        NewsClaim.extractor_version == extractor_version,
    )
    if not include_superseded:
        q = q.where(NewsClaim.status == "extracted")
    q = q.order_by(NewsClaim.created_at.asc(), NewsClaim.id.asc())
    return list((await session.execute(q)).scalars().all())


async def _upsert_run(
    session,
    *,
    run: NewsClaimExtraction | None,
    article_id: uuid.UUID,
    extractor_version: str,
    content_fingerprint: str,
    status: str,
    provider: str | None,
    model: str | None,
    claim_count: int,
    error_class: str | None,
    error_message: str | None,
) -> NewsClaimExtraction:
    now = _utc_now()
    if run is None:
        run = NewsClaimExtraction(
            article_id=article_id,
            extractor_version=extractor_version,
            content_fingerprint=content_fingerprint,
            status=status,
            provider=provider,
            model=model,
            claim_count=claim_count,
            error_class=error_class,
            error_message=error_message,
            completed_at=now if status in ("succeeded", "failed", "skipped") else None,
        )
        session.add(run)
    else:
        run.content_fingerprint = content_fingerprint
        run.status = status
        run.provider = provider
        run.model = model
        run.claim_count = claim_count
        run.error_class = error_class
        run.error_message = error_message
        run.completed_at = now if status in ("succeeded", "failed", "skipped") else None
    await session.flush()
    return run
