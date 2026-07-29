"""Deterministic EventPackage hero selection (RSS candidates)."""
from __future__ import annotations

from services.news.hero_selection import ImageCandidate, select_hero_image


def test_rejects_http_and_tracking_and_tiny():
    picked = select_hero_image(
        [
            ImageCandidate(
                url="http://example.com/a.jpg",
                source_article_id="a1",
                is_primary=True,
            ),
            ImageCandidate(
                url="https://example.com/pixel.gif",
                source_article_id="a2",
                is_primary=True,
            ),
            ImageCandidate(
                url="https://cdn.example.com/photo.jpg",
                source_article_id="a3",
                width=32,
                height=32,
                is_primary=True,
            ),
        ]
    )
    assert picked is None


def test_prefers_primary_and_is_stable():
    cands = [
        ImageCandidate(
            url="https://cdn.example.com/b.jpg",
            source_article_id="b",
            is_primary=False,
            article_sort_key="b",
        ),
        ImageCandidate(
            url="https://cdn.example.com/a.jpg",
            source_article_id="a",
            is_primary=True,
            article_sort_key="a",
        ),
        ImageCandidate(
            url="https://cdn.example.com/a.jpg#frag",
            source_article_id="a-dup",
            is_primary=False,
            article_sort_key="z",
        ),
    ]
    first = select_hero_image(cands)
    second = select_hero_image(list(reversed(cands)))
    assert first is not None
    assert first["url"] == "https://cdn.example.com/a.jpg"
    assert first["source_article_id"] == "a"
    assert first["origin"] == "rss"
    assert "primary_article" in (first["selection_reason"] or "")
    assert first["selection_score"] == first["hero_confidence"]
    assert 0.0 <= float(first["selection_score"]) <= 1.0
    assert second["url"] == first["url"]
    assert second["source_article_id"] == first["source_article_id"]
    assert second["selection_score"] == first["selection_score"]


def test_tie_break_by_article_id_then_url():
    picked = select_hero_image(
        [
            ImageCandidate(
                url="https://cdn.example.com/z.jpg",
                source_article_id="z",
                is_primary=True,
                article_sort_key="z",
            ),
            ImageCandidate(
                url="https://cdn.example.com/a.jpg",
                source_article_id="a",
                is_primary=True,
                article_sort_key="a",
            ),
        ]
    )
    assert picked is not None
    assert picked["source_article_id"] == "a"
    assert picked["url"] == "https://cdn.example.com/a.jpg"


def test_larger_known_area_wins_among_non_primary():
    picked = select_hero_image(
        [
            ImageCandidate(
                url="https://cdn.example.com/small.jpg",
                source_article_id="s",
                width=100,
                height=100,
                is_primary=False,
                article_sort_key="s",
            ),
            ImageCandidate(
                url="https://cdn.example.com/large.jpg",
                source_article_id="l",
                width=800,
                height=600,
                is_primary=False,
                article_sort_key="l",
            ),
        ]
    )
    assert picked is not None
    assert picked["url"] == "https://cdn.example.com/large.jpg"
