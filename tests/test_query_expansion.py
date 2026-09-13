"""Fail-closed lexical expansion. No benchmark-id hardcoding."""
from __future__ import annotations

from pathlib import Path

from services.workspace_files.chunk_retriever import build_or_tsquery, normalize_query_tokens
from services.workspace_files.query_expansion import extra_tsquery_atoms, lexical_expand_mode, expansion_trace

EXPAND_SRC = Path(__file__).resolve().parents[1] / "services" / "workspace_files" / "query_expansion.py"


def test_default_mode_is_off(monkeypatch):
    monkeypatch.delenv("BEN_FTS_LEXICAL_EXPAND", raising=False)
    assert lexical_expand_mode() == "off"
    assert extra_tsquery_atoms(["delivered", "goods"]) == []


def test_prefix_expands_latin_inflection_not_short_tokens(monkeypatch):
    monkeypatch.setenv("BEN_FTS_LEXICAL_EXPAND", "prefix")
    atoms = extra_tsquery_atoms(["delivered", "goods", "must"])
    assert "deliver:*" in atoms
    assert "goods:*" not in atoms
    assert "must:*" not in atoms


def test_variants_are_exact_stems(monkeypatch):
    monkeypatch.setenv("BEN_FTS_LEXICAL_EXPAND", "variants")
    assert extra_tsquery_atoms(["delivered"]) == ["deliver"]
    assert extra_tsquery_atoms(["delivering"]) == ["deliver"]


def test_hebrew_is_not_expanded(monkeypatch):
    monkeypatch.setenv("BEN_FTS_LEXICAL_EXPAND", "prefix")
    assert extra_tsquery_atoms(["זכויות", "סיום"]) == []


def test_user_operator_tokens_never_become_atoms():
    q = build_or_tsquery(["delivered:*", "foo:*", "notice"])
    assert q == "notice"
    assert ":*" not in (q or "")


def test_build_or_tsquery_default_unchanged(monkeypatch):
    monkeypatch.delenv("BEN_FTS_LEXICAL_EXPAND", raising=False)
    tokens = normalize_query_tokens("Where must the goods be delivered?")
    q = build_or_tsquery(tokens)
    assert q is not None
    assert ":*" not in q
    assert "delivered" in q


def test_build_or_tsquery_prefix_appends_stem(monkeypatch):
    monkeypatch.setenv("BEN_FTS_LEXICAL_EXPAND", "prefix")
    tokens = normalize_query_tokens("Where must the goods be delivered?")
    q = build_or_tsquery(tokens)
    assert q is not None
    assert "delivered" in q
    assert "deliver:*" in q


def test_no_benchmark_literals_in_expander():
    body = EXPAND_SRC.read_text(encoding="utf-8").casefold()
    for needle in ("m09", "hamelacha", "ben-gold", "named delivery place"):
        assert needle not in body


def test_unknown_mode_is_off(monkeypatch):
    monkeypatch.setenv("BEN_FTS_LEXICAL_EXPAND", "embeddings")
    assert lexical_expand_mode() == "off"
    assert extra_tsquery_atoms(["delivered"], mode="embeddings") == []


def test_expansion_trace_off_has_zero_atoms(monkeypatch):
    monkeypatch.delenv("BEN_FTS_LEXICAL_EXPAND", raising=False)
    tokens = normalize_query_tokens("Where must the goods be delivered?")
    trace = expansion_trace(tokens)
    assert trace["lexical_expand_mode"] == "off"
    assert trace["expansion_atom_count"] == 0
    assert "delivered" in trace["query_tokens"]


def test_expansion_trace_prefix_counts_atoms(monkeypatch):
    monkeypatch.setenv("BEN_FTS_LEXICAL_EXPAND", "prefix")
    tokens = normalize_query_tokens("Where must the goods be delivered?")
    trace = expansion_trace(tokens)
    assert trace["lexical_expand_mode"] == "prefix"
    assert trace["expansion_atom_count"] == 1
    assert extra_tsquery_atoms(tokens) == ["deliver:*"]
