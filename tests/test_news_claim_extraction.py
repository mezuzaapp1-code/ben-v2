"""E1 NewsClaim extraction — contract, idempotency, provenance, API auth."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.news.claim_contract import (  # noqa: E402
    EXTRACTOR_VERSION,
    ExtractedClaim,
    claim_fingerprint,
    content_fingerprint,
    parse_extracted_claims,
)
from services.news.claim_extractor import (  # noqa: E402
    extract_claims,
    extract_claims_heuristic,
)
from services.news import claim_extraction_service as ces  # noqa: E402

ORG_A = "11111111-1111-1111-1111-111111111111"
ARTICLE_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
T0 = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)


def _admin_claims():
    return {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }


def _article(**over):
    base = SimpleNamespace(
        id=ARTICLE_ID,
        source_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        guid="g1",
        title="Acme raises guidance after strong quarter",
        url="https://example.com/a",
        summary=(
            "Acme Corp said revenue was $30 billion. "
            "Shares rose 4% in after-hours trading. "
            "Analysts said the results could pave the way for further expansion."
        ),
        image_url=None,
        published_at=T0,
        category="markets",
        created_at=T0,
    )
    for k, v in over.items():
        setattr(base, k, v)
    return base


# --- Contract / classification -------------------------------------------------


def test_implication_must_be_interpretive():
    with pytest.raises(ValidationError):
        ExtractedClaim(
            text="This could pave the way for expansion",
            claim_type="implication",
            role="factual",
            source_field="summary",
            source_excerpt="This could pave the way for expansion",
            source_start=0,
            source_end=10,
        )


def test_parse_malformed_model_output():
    with pytest.raises((ValidationError, ValueError)):
        parse_extracted_claims({"claims": [{"text": "x"}]})  # missing required fields


def test_heuristic_separates_factual_and_interpretive():
    result = extract_claims_heuristic(
        title="Acme raises guidance after strong quarter",
        summary=(
            "Acme Corp said revenue was $30 billion. "
            "Shares rose 4% in after-hours trading. "
            "Analysts said the results could pave the way for further expansion."
        ),
    )
    types = {c.claim_type for c in result.claims}
    assert "metric" in types or "occurrence" in types
    assert "market" in types
    impl = [c for c in result.claims if c.claim_type == "implication"]
    assert impl
    assert all(c.role == "interpretive" for c in impl)
    factual = [c for c in result.claims if c.role == "factual"]
    assert factual


def test_provenance_source_span_preserved():
    summary = "Police said the suspect allegedly fled the scene."
    result = extract_claims_heuristic(title="Suspect flees", summary=summary)
    assert result.claims
    c = result.claims[0]
    assert c.source_field == "summary"
    assert c.source_excerpt
    assert c.attribution or "said" in c.text.lower() or c.uncertainty
    assert c.uncertainty is not None  # allegedly
    if c.source_start is not None and c.source_end is not None:
        assert summary[c.source_start : c.source_end]


def test_uncertainty_and_attribution_preserved_in_text():
    summary = "According to regulators, the firm allegedly underreported liabilities."
    result = extract_claims_heuristic(title="Regulators probe firm", summary=summary)
    assert result.claims
    joined = " ".join(c.text for c in result.claims)
    assert "allegedly" in joined.lower() or any(c.uncertainty for c in result.claims)
    assert any(
        (c.attribution and "according" in c.attribution.lower())
        or "according to" in c.text.lower()
        for c in result.claims
    )


def test_headline_not_extracted_without_summary_support():
    result = extract_claims_heuristic(
        title="Completely unrelated headline about unicorns",
        summary="The central bank held interest rates steady at 5.25%.",
    )
    title_claims = [c for c in result.claims if c.source_field == "title"]
    assert title_claims == []


def test_allegation_not_unqualified_fact():
    summary = "Sources say the executive allegedly accepted bribes."
    result = extract_claims_heuristic(title="Probe widens", summary=summary)
    assert result.claims
    for c in result.claims:
        # Must retain hedging in text or uncertainty/attribution fields.
        soft = (
            "allegedly" in c.text.lower()
            or "sources say" in c.text.lower()
            or bool(c.uncertainty)
            or bool(c.attribution)
        )
        assert soft


@pytest.mark.asyncio
async def test_llm_malformed_output_raises():
    async def bad_llm(_title, _summary):
        return "not-json{{"

    with pytest.raises(ValueError, match="malformed_model_output"):
        await extract_claims(title="T", summary="S said hello world today.", llm_extract_fn=bad_llm)


# --- Service idempotency / retry ----------------------------------------------


class _Session:
    def __init__(self, article, runs=None, claims=None):
        self.article = article
        self.runs = list(runs or [])
        self.claims = list(claims or [])
        self.deleted = []
        self.added = []
        self._executed_updates = 0

    async def get(self, model, pk):
        if model.__name__ == "NewsArticle" and pk == self.article.id:
            return self.article
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        result = MagicMock()
        if "news_claim_extractions" in sql.lower() or "NewsClaimExtraction" in sql:
            run = self.runs[0] if self.runs else None
            result.scalar_one_or_none = MagicMock(return_value=run)
            result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=self.runs)))
            return result
        if "UPDATE" in sql.upper() or "update" in type(stmt).__name__.lower():
            self._executed_updates += 1
            return result
        # claim select
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=list(self.claims))))
        result.scalar_one_or_none = MagicMock(return_value=None)
        return result

    def add(self, obj):
        self.added.append(obj)
        if obj.__class__.__name__ == "NewsClaimExtraction":
            self.runs = [obj]
        if obj.__class__.__name__ == "NewsClaim":
            self.claims.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)
        if obj in self.claims:
            self.claims.remove(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = T0
        if getattr(obj, "completed_at", None) is None and getattr(obj, "status", None) in (
            "succeeded",
            "failed",
        ):
            obj.completed_at = T0


class _CM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_extract_idempotent_same_version():
    article = _article()
    fp = content_fingerprint(title=article.title, summary=article.summary)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=ARTICLE_ID,
        extractor_version=EXTRACTOR_VERSION,
        content_fingerprint=fp,
        status="succeeded",
        provider="heuristic",
        model="rules-e1.1",
        claim_count=1,
        error_class=None,
        error_message=None,
        created_at=T0,
        completed_at=T0,
    )
    claim = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=ARTICLE_ID,
        claim_fingerprint="abc",
        claim_text="Acme Corp said revenue was $30 billion.",
        claim_type="metric",
        role="factual",
        source_field="summary",
        source_excerpt="Acme Corp said revenue was $30 billion.",
        source_start=0,
        source_end=40,
        attribution="Acme Corp said",
        uncertainty=None,
        status="extracted",
        extractor_version=EXTRACTOR_VERSION,
        provider="heuristic",
        model="rules-e1.1",
        created_at=T0,
    )
    session = _Session(article, runs=[run], claims=[claim])
    with patch.object(ces, "get_db_session", return_value=_CM(session)):
        out1 = await ces.extract_article_claims(ARTICLE_ID)
        out2 = await ces.extract_article_claims(ARTICLE_ID)
    assert out1["idempotent"] is True
    assert out2["idempotent"] is True
    assert len(out1["claims"]) == 1
    assert out1["claims"][0]["text"] == claim.claim_text


@pytest.mark.asyncio
async def test_retry_after_extraction_failure():
    article = _article()
    fp = content_fingerprint(title=article.title, summary=article.summary)
    failed = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=ARTICLE_ID,
        extractor_version=EXTRACTOR_VERSION,
        content_fingerprint=fp,
        status="failed",
        provider=None,
        model=None,
        claim_count=0,
        error_class="malformed_model_output",
        error_message="malformed_model_output: boom",
        created_at=T0,
        completed_at=T0,
    )
    session = _Session(article, runs=[failed], claims=[])

    async def ok_llm(title, summary):
        return {
            "provider": "openai",
            "model": "gpt-test",
            "claims": [
                {
                    "text": "Acme Corp said revenue was $30 billion.",
                    "claim_type": "metric",
                    "role": "factual",
                    "source_field": "summary",
                    "source_excerpt": "Acme Corp said revenue was $30 billion.",
                    "source_start": 0,
                    "source_end": 39,
                    "attribution": "Acme Corp said",
                    "uncertainty": None,
                }
            ],
        }

    with patch.object(ces, "get_db_session", return_value=_CM(session)):
        out = await ces.extract_article_claims(ARTICLE_ID, llm_extract_fn=ok_llm)
    assert out["idempotent"] is False
    assert out["extraction"]["status"] == "succeeded"
    assert len(out["claims"]) == 1
    assert out["claims"][0]["provider"] == "openai"


@pytest.mark.asyncio
async def test_malformed_llm_marks_failed_run():
    article = _article()
    session = _Session(article, runs=[], claims=[])

    async def bad_llm(_t, _s):
        return "{{{not json"

    with patch.object(ces, "get_db_session", return_value=_CM(session)):
        out = await ces.extract_article_claims(ARTICLE_ID, llm_extract_fn=bad_llm)
    assert out["extraction"]["status"] == "failed"
    assert out["extraction"]["error_class"] == "malformed_model_output"
    assert out["claims"] == []


@pytest.mark.asyncio
async def test_reextraction_new_version_supersedes():
    article = _article()
    session = _Session(article, runs=[], claims=[])
    old = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=ARTICLE_ID,
        claim_fingerprint="old",
        claim_text="old claim",
        claim_type="occurrence",
        role="factual",
        source_field="summary",
        source_excerpt="old claim",
        source_start=0,
        source_end=9,
        attribution=None,
        uncertainty=None,
        status="extracted",
        extractor_version="e1.0",
        provider="heuristic",
        model="rules-e1.0",
        created_at=T0,
    )
    session.claims = [old]

    with patch.object(ces, "get_db_session", return_value=_CM(session)):
        out = await ces.extract_article_claims(ARTICLE_ID, extractor_version="e1.2")
    assert out["extraction"]["status"] == "succeeded"
    assert session._executed_updates >= 1


# --- Migration offline SQL ----------------------------------------------------


def test_migration_008_upgrade_downgrade_wired():
    import importlib.util
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from unittest.mock import MagicMock, patch

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "database" / "migrations" / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("008_news_claims_e1")
    assert rev is not None
    assert rev.down_revision == "007_news_event_packages_v1"
    assert script.get_current_head() == "008_news_claims_e1"

    path = root / "database" / "migrations" / "versions" / "008_news_claims_e1.py"
    spec = importlib.util.spec_from_file_location("mig_008_news_claims_e1", path)
    assert spec and spec.loader
    m008 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m008)

    mock_op = MagicMock()
    with patch.object(m008, "op", mock_op):
        m008.upgrade()
        assert mock_op.create_table.call_count == 2
        table_names = [c.args[0] for c in mock_op.create_table.call_args_list]
        assert table_names == ["news_claim_extractions", "news_claims"]
        m008.downgrade()
        dropped = [c.args[0] for c in mock_op.drop_table.call_args_list]
        assert dropped == ["news_claims", "news_claim_extractions"]


# --- HTTP / OpenAPI -----------------------------------------------------------


def test_claims_routes_require_auth():
    client = TestClient(main.app)
    aid = str(ARTICLE_ID)
    assert client.post(f"/api/internal/news/articles/{aid}/claims/extract").status_code == 401
    assert client.get(f"/api/internal/news/articles/{aid}/claims").status_code == 401
    assert client.get(f"/api/internal/news/articles/{aid}/claims/extraction").status_code == 401


def test_claims_openapi_paths_present():
    client = TestClient(main.app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/internal/news/articles/{article_id}/claims/extract" in paths
    assert "/api/internal/news/articles/{article_id}/claims" in paths
    assert "/api/internal/news/articles/{article_id}/claims/extraction" in paths


def test_extract_http_success():
    client = TestClient(main.app)
    payload = {
        "idempotent": False,
        "extraction": {"status": "succeeded", "claim_count": 1},
        "claims": [{"id": str(uuid.uuid4()), "text": "x", "claim_type": "metric"}],
        "request_id": "r1",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.claim_extraction_service.extract_article_claims",
        new=AsyncMock(return_value=payload),
    ):
        r = client.post(
            f"/api/internal/news/articles/{ARTICLE_ID}/claims/extract",
            headers={"Authorization": "Bearer t"},
        )
    assert r.status_code == 200
    assert r.json()["extraction"]["status"] == "succeeded"


def test_fingerprint_stable():
    a = claim_fingerprint(
        text="Revenue was $30B",
        claim_type="metric",
        source_field="summary",
        source_start=0,
        source_end=16,
    )
    b = claim_fingerprint(
        text="  Revenue was $30B ",
        claim_type="metric",
        source_field="summary",
        source_start=0,
        source_end=16,
    )
    assert a == b
