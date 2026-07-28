"""Curated BEN News seed feeds (AI / technology). Tier is catalog metadata only."""
from __future__ import annotations

from typing import TypedDict


class SeedSource(TypedDict):
    name: str
    feed_url: str
    category: str
    language: str
    enabled: bool
    tier: str


# Keep categories in {"ai", "technology", "tech"} for Pass A eligibility.
CURATED_NEWS_SOURCES: tuple[SeedSource, ...] = (
    {
        "name": "OpenAI Blog",
        "feed_url": "https://openai.com/blog/rss.xml",
        "category": "ai",
        "language": "en",
        "enabled": True,
        "tier": "official",
    },
    {
        "name": "Google AI Blog",
        "feed_url": "https://blog.google/technology/ai/rss/",
        "category": "ai",
        "language": "en",
        "enabled": True,
        "tier": "official",
    },
    {
        "name": "Google DeepMind Blog",
        "feed_url": "https://deepmind.google/blog/rss.xml",
        "category": "ai",
        "language": "en",
        "enabled": True,
        "tier": "official",
    },
    {
        "name": "Hugging Face Blog",
        "feed_url": "https://huggingface.co/blog/feed.xml",
        "category": "ai",
        "language": "en",
        "enabled": True,
        "tier": "official",
    },
    {
        "name": "NVIDIA Blog",
        "feed_url": "https://blogs.nvidia.com/feed/",
        "category": "technology",
        "language": "en",
        "enabled": True,
        "tier": "official",
    },
    {
        "name": "AWS Machine Learning Blog",
        "feed_url": "https://aws.amazon.com/blogs/machine-learning/feed/",
        "category": "ai",
        "language": "en",
        "enabled": True,
        "tier": "official",
    },
    {
        "name": "Microsoft Research Blog",
        "feed_url": "https://www.microsoft.com/en-us/research/feed/",
        "category": "ai",
        "language": "en",
        "enabled": True,
        "tier": "official",
    },
    {
        "name": "MIT Technology Review",
        "feed_url": "https://www.technologyreview.com/feed/",
        "category": "technology",
        "language": "en",
        "enabled": True,
        "tier": "publication",
    },
    {
        "name": "The Verge",
        "feed_url": "https://www.theverge.com/rss/index.xml",
        "category": "technology",
        "language": "en",
        "enabled": True,
        "tier": "publication",
    },
    {
        "name": "Ars Technica",
        "feed_url": "https://feeds.arstechnica.com/arstechnica/index",
        "category": "technology",
        "language": "en",
        "enabled": True,
        "tier": "publication",
    },
    {
        "name": "TechCrunch",
        "feed_url": "https://techcrunch.com/feed/",
        "category": "technology",
        "language": "en",
        "enabled": True,
        "tier": "publication",
    },
    {
        "name": "Wired",
        "feed_url": "https://www.wired.com/feed/rss",
        "category": "technology",
        "language": "en",
        "enabled": True,
        "tier": "publication",
    },
    {
        "name": "The Register",
        "feed_url": "https://www.theregister.com/headlines.atom",
        "category": "technology",
        "language": "en",
        "enabled": True,
        "tier": "publication",
    },
    {
        "name": "Nature News",
        "feed_url": "https://www.nature.com/nature.rss",
        "category": "technology",
        "language": "en",
        "enabled": True,
        "tier": "publication",
    },
)
