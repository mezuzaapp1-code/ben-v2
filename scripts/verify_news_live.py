"""One-shot production news verification (run via: railway run python scripts/verify_news_live.py)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> int:
    from sqlalchemy import text

    from database.connection import get_db_session
    from services.news.product_news_api import get_top_news

    async with get_db_session() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM ben.news_sources) AS sources,
                      (SELECT count(*) FROM ben.news_sources WHERE enabled) AS sources_enabled,
                      (SELECT count(*) FROM ben.news_articles) AS articles,
                      (SELECT count(*) FROM ben.news_events) AS events,
                      (SELECT count(*) FROM ben.news_event_packages) AS packages
                    """
                )
            )
        ).mappings().one()
    print("COUNTS", json.dumps(dict(row), default=str))

    top = await get_top_news(limit=10)
    items = list(top.get("items") or [])
    print("TOP_COUNT", len(items))
    for item in items[:5]:
        headline = (
            item.get("headline")
            or item.get("title")
            or (item.get("package") or {}).get("headline")
            or item.get("event_id")
        )
        print("TOP_ITEM", str(headline)[:160])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
