"""Existing-capabilities audit for lexical FTS failures.

Probes the current Postgres simple FTS stack. Does not add dependencies.
Verdicts are USE / ADAPT / LEARN / REJECT.
"""

from __future__ import annotations

import os
from typing import Any

from services.improvement.schema import CapabilityItem

GENERIC_DOC = "the delivery window closed after inspection"
GENERIC_EXACT = "delivered"
GENERIC_PREFIX = "deliver:*"
GENERIC_SURFACE = "delivery"


def _dsn() -> str:
    raw = os.getenv("BEN_TEST_PG_DSN") or os.getenv("DATABASE_URL") or ""
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def audit_static() -> list[CapabilityItem]:
    return [
        CapabilityItem(
            name="postgres_simple_fts_prefix_operator",
            verdict="USE",
            reason=(
                "Existing generated text_tsv uses to_tsvector('simple', text) plus "
                "GIN. to_tsquery('simple', 'stem:*') already matches inflectional "
                "variants on that index. No schema change, no new extension."
            ),
        ),
        CapabilityItem(
            name="query_prep_sanitize_and_or_join",
            verdict="ADAPT",
            reason=(
                "build_or_tsquery already sanitizes user tokens and OR-joins them. "
                "I1 adapts it to append expander-generated stem:* atoms when "
                "BEN_FTS_LEXICAL_EXPAND=prefix. Default remains off."
            ),
        ),
        CapabilityItem(
            name="english_text_search_config",
            verdict="REJECT",
            reason=(
                "Switching the generated column to 'english' is a schema change "
                "(forbidden). Snowball delivered≠delivery stemming is also not "
                "guaranteed for this pair, and Hebrew tokens would be mishandled."
            ),
        ),
        CapabilityItem(
            name="embeddings_hybrid_rerank_vector_db",
            verdict="REJECT",
            reason=(
                "I1 forbids embeddings, hybrid retrieval, rerankers, and new vector "
                "infrastructure. Lexical prefix matching is in-stack."
            ),
        ),
        CapabilityItem(
            name="new_python_dependency",
            verdict="REJECT",
            reason="Current Postgres FTS already provides a sufficient mechanism.",
        ),
        CapabilityItem(
            name="provider_native_pdf",
            verdict="REJECT",
            reason="Out of scope; Gate P already measured this as blocked without keys.",
        ),
        CapabilityItem(
            name="learn_new_stemmer",
            verdict="LEARN",
            reason=(
                "If prefix atoms over-expand or under-stem a future class, record "
                "the pair for a later gated morphology table. Do not add one in I1."
            ),
        ),
    ]


async def probe_postgres() -> dict[str, Any]:
    """Live probe of simple vs english vs prefix. Generic wording only."""
    out: dict[str, Any] = {"available": False, "pairs": []}
    try:
        import asyncpg
    except Exception as exc:  # pragma: no cover
        out["error"] = f"asyncpg missing: {exc}"
        return out
    dsn = _dsn()
    if not dsn:
        out["error"] = "no DATABASE_URL / BEN_TEST_PG_DSN"
        return out
    conn = await asyncpg.connect(dsn)
    try:
        results = []
        probes = [
            (
                "simple_exact_inflection",
                "SELECT to_tsvector('simple', $1) @@ to_tsquery('simple', $2)",
                GENERIC_DOC,
                GENERIC_EXACT,
            ),
            (
                "simple_prefix_inflection",
                "SELECT to_tsvector('simple', $1) @@ to_tsquery('simple', $2)",
                GENERIC_DOC,
                GENERIC_PREFIX,
            ),
            (
                "simple_surface_form",
                "SELECT to_tsvector('simple', $1) @@ to_tsquery('simple', $2)",
                GENERIC_DOC,
                GENERIC_SURFACE,
            ),
            (
                "english_query_on_simple_vec",
                "SELECT to_tsvector('simple', $1) @@ to_tsquery('english', $2)",
                GENERIC_DOC,
                GENERIC_EXACT,
            ),
            (
                "english_english_inflection",
                "SELECT to_tsvector('english', $1) @@ to_tsquery('english', $2)",
                GENERIC_DOC,
                GENERIC_EXACT,
            ),
        ]
        for name, sql, doc, q in probes:
            matched = await conn.fetchval(sql, doc, q)
            results.append({"name": name, "query": q, "matched": bool(matched)})
        out["available"] = True
        out["document"] = GENERIC_DOC
        out["pairs"] = results
        return out
    finally:
        await conn.close()


def report(static: list[CapabilityItem] | None = None, probe: dict[str, Any] | None = None) -> dict[str, Any]:
    items = static or audit_static()
    return {
        "principle": "existing-capabilities-first; no new dependency if current infra suffices",
        "items": [i.to_dict() for i in items],
        "use": [i.name for i in items if i.verdict == "USE"],
        "adapt": [i.name for i in items if i.verdict == "ADAPT"],
        "learn": [i.name for i in items if i.verdict == "LEARN"],
        "reject": [i.name for i in items if i.verdict == "REJECT"],
        "postgres_probe": probe or {},
        "chosen_mechanism": (
            "Fail-closed query-prep prefix atoms (stem:*) against existing "
            "to_tsvector('simple') GIN. Default BEN_FTS_LEXICAL_EXPAND=off."
        ),
    }
