"""E1 NewsClaim multi-axis classification — contract, safety, idempotency, API."""
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
    derived_role,
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


def _base_claim(**over) -> dict:
    data = {
        "text": "Acme Corp said revenue was $30 billion.",
        "epistemic_type": "attributed_statement",
        "semantic_domains": ["company", "financial"],
        "source_strength": "unknown",
        "source_field": "summary",
        "source_excerpt": "Acme Corp said revenue was $30 billion.",
        "source_start": 0,
        "source_end": 39,
        "attribution": "Acme Corp said",
        "uncertainty": None,
        "corrects_ref": None,
    }
    data.update(over)
    return data


# --- Contract / epistemic safety ---------------------------------------------


def test_multi_label_semantic_domains():
    c = ExtractedClaim.model_validate(
        _base_claim(semantic_domains=["company", "financial", "market"])
    )
    assert c.semantic_domains == ["company", "financial", "market"]


def test_epistemic_exclusivity_and_derived_role():
    assert derived_role("fact") == "factual"
    assert derived_role("prediction") == "interpretive"
    assert derived_role("opinion") == "interpretive"
    c = ExtractedClaim.model_validate(_base_claim(epistemic_type="prediction", attribution=None))
    # prediction with attribution optional
    c = ExtractedClaim.model_validate(
        _base_claim(
            text="Results could pave the way for expansion.",
            epistemic_type="prediction",
            attribution=None,
            semantic_domains=["company"],
        )
    )
    assert c.role == "interpretive"


def test_opinion_prediction_not_factual_projection():
    for epi in ("opinion", "prediction"):
        c = ExtractedClaim.model_validate(
            _base_claim(
                text="In our view growth will accelerate next year.",
                epistemic_type=epi,
                attribution=None,
                semantic_domains=["company"],
            )
        )
        assert derived_role(c.epistemic_type) == "interpretive"


def test_allegation_requires_attribution():
    with pytest.raises(ValidationError):
        ExtractedClaim.model_validate(
            _base_claim(
                text="The executive allegedly accepted bribes.",
                epistemic_type="allegation",
                attribution=None,
                uncertainty="allegedly",
            )
        )


def test_attributed_statement_requires_attribution():
    with pytest.raises(ValidationError):
        ExtractedClaim.model_validate(
            _base_claim(epistemic_type="attributed_statement", attribution=None)
        )


def test_correction_requires_corrects_ref():
    with pytest.raises(ValidationError):
        ExtractedClaim.model_validate(
            _base_claim(
                text="Correction: revenue was $28 billion, not $30 billion.",
                epistemic_type="correction",
                attribution=None,
                corrects_ref=None,
                semantic_domains=["financial"],
            )
        )
    ok = ExtractedClaim.model_validate(
        _base_claim(
            text="Correction: revenue was $28 billion, not $30 billion.",
            epistemic_type="correction",
            attribution=None,
            corrects_ref="prior revenue figure of $30 billion",
            semantic_domains=["financial"],
        )
    )
    assert ok.corrects_ref


def test_source_strength_is_provenance_not_confidence():
    c = ExtractedClaim.model_validate(_base_claim(source_strength="wire"))
    dumped = c.model_dump()
    assert "confidence" not in dumped
    assert dumped["source_strength"] == "wire"
    # distinct from truth confidence — field name is source_strength only
    assert set(dumped.keys()) >= {"source_strength", "epistemic_type", "semantic_domains"}


def test_claim_type_not_in_contract():
    with pytest.raises(ValidationError):
        ExtractedClaim.model_validate({**_base_claim(), "claim_type": "metric"})


def test_parse_malformed_model_output():
    with pytest.raises((ValidationError, ValueError)):
        parse_extracted_claims({"claims": [{"text": "x"}]})


def test_heuristic_axes_and_provenance():
    result = extract_claims_heuristic(
        title="Acme raises guidance after strong quarter — Reuters",
        summary=(
            "Acme Corp said revenue was $30 billion. "
            "Shares rose 4% in after-hours trading. "
            "Analysts said the results could pave the way for further expansion. "
            "Sources say an executive allegedly accepted bribes."
        ),
    )
    assert result.claims
    types = {c.epistemic_type for c in result.claims}
    assert "attributed_statement" in types or "fact" in types
    assert "prediction" in types
    assert any(len(c.semantic_domains) >= 1 for c in result.claims)
    multi = [c for c in result.claims if len(c.semantic_domains) > 1]
    assert multi  # e.g. company+financial or market+financial
    assert any(c.source_strength == "wire" for c in result.claims)
    pred = [c for c in result.claims if c.epistemic_type == "prediction"]
    assert pred and all(c.role == "interpretive" for c in pred)
    al = [c for c in result.claims if c.epistemic_type == "allegation"]
    assert al and all(c.attribution for c in al)


def test_uncertainty_preserved_in_text():
    summary = "According to regulators, the firm allegedly underreported liabilities."
    result = extract_claims_heuristic(title="Regulators probe firm", summary=summary)
    assert result.claims
    joined = " ".join(c.text for c in result.claims)
    assert "allegedly" in joined.lower()
    assert any(c.uncertainty for c in result.claims)


def test_provenance_source_span_preserved():
    summary = "Police said the suspect allegedly fled the scene."
    result = extract_claims_heuristic(title="Suspect flees", summary=summary)
    assert result.claims
    c = result.claims[0]
    assert c.source_field == "summary"
    assert c.source_excerpt
    if c.source_start is not None and c.source_end is not None:
        assert summary[c.source_start : c.source_end]


@pytest.mark.asyncio
async def test_llm_malformed_output_raises():
    async def bad_llm(_title, _summary):
        return "not-json{{"

    with pytest.raises(ValueError, match="malformed_model_output"):
        await extract_claims(title="T", summary="S said hello world today.", llm_extract_fn=bad_llm)


# --- Service -----------------------------------------------------------------


class _Session:
    def __init__(self, article, runs=None, claims=None):
        self.article = article
        self.runs = list(runs or [])
        self.claims = list(claims or [])
        self.added = []
        self.deleted = []

    async def get(self, model, pk):
        if model.__name__ == "NewsArticle" and pk == self.article.id:
            return self.article
        return None

    async def execute(self, stmt):
        sql = str(stmt).lower()
        result = MagicMock()
        if "news_claim_extractions" in sql or "newsclaimextraction" in sql:
            run = self.runs[0] if self.runs else None
            result.scalar_one_or_none = MagicMock(return_value=run)
            return result
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


def _claim_row(**over):
    base = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=ARTICLE_ID,
        claim_fingerprint="abc",
        claim_text="Acme Corp said revenue was $30 billion.",
        epistemic_type="attributed_statement",
        semantic_domains=["company", "financial"],
        source_strength="unknown",
        source_field="summary",
        source_excerpt="Acme Corp said revenue was $30 billion.",
        source_start=0,
        source_end=40,
        attribution="Acme Corp said",
        uncertainty=None,
        corrects_ref=None,
        status="extracted",
        extractor_version=EXTRACTOR_VERSION,
        provider="heuristic",
        model="rules-e1.2",
        created_at=T0,
    )
    for k, v in over.items():
        setattr(base, k, v)
    return base


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
        model="rules-e1.2",
        claim_count=1,
        error_class=None,
        error_message=None,
        created_at=T0,
        completed_at=T0,
    )
    claim = _claim_row()
    session = _Session(article, runs=[run], claims=[claim])
    with patch.object(ces, "get_db_session", return_value=_CM(session)):
        out1 = await ces.extract_article_claims(ARTICLE_ID)
        out2 = await ces.extract_article_claims(ARTICLE_ID)
    assert out1["idempotent"] is True
    assert out2["idempotent"] is True
    assert out1["claims"][0]["epistemic_type"] == "attributed_statement"
    assert "claim_type" not in out1["claims"][0]
    assert out1["claims"][0]["derived_role"] == "factual"
    assert session.deleted == []


@pytest.mark.asyncio
async def test_reclassification_new_version_does_not_mutate_old_claims():
    article = _article()
    old = _claim_row(extractor_version="e1.1", epistemic_type="fact", attribution=None)
    session = _Session(article, runs=[], claims=[])
    # Old claims live under other version; new version insert must not delete them.
    # Simulate by tracking deletes only — service loads claims for target version only.
    with patch.object(ces, "get_db_session", return_value=_CM(session)):
        out = await ces.extract_article_claims(ARTICLE_ID, extractor_version="e1.3")
    assert out["extraction"]["status"] == "succeeded"
    assert session.deleted == []
    assert out["extraction"]["extractor_version"] == "e1.3"


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
            "claims": [_base_claim()],
        }

    with patch.object(ces, "get_db_session", return_value=_CM(session)):
        out = await ces.extract_article_claims(ARTICLE_ID, llm_extract_fn=ok_llm)
    assert out["idempotent"] is False
    assert out["extraction"]["status"] == "succeeded"
    assert out["claims"][0]["source_strength"] == "unknown"
    assert "confidence" not in out["claims"][0]


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
        # Ensure new columns present in create_table kwargs/args
        claims_call = mock_op.create_table.call_args_list[1]
        col_names = [a.name for a in claims_call.args[1:] if hasattr(a, "name")]
        assert "epistemic_type" in col_names
        assert "semantic_domains" in col_names
        assert "source_strength" in col_names
        assert "claim_type" not in col_names
        assert "role" not in col_names
        m008.downgrade()
        dropped = [c.args[0] for c in mock_op.drop_table.call_args_list]
        assert dropped == ["news_claims", "news_claim_extractions"]


def test_fingerprint_stable():
    a = claim_fingerprint(
        text="Revenue was $30B",
        epistemic_type="fact",
        semantic_domains=["financial", "company"],
        source_field="summary",
        source_start=0,
        source_end=16,
    )
    b = claim_fingerprint(
        text="  Revenue was $30B ",
        epistemic_type="fact",
        semantic_domains=["company", "financial"],
        source_field="summary",
        source_start=0,
        source_end=16,
    )
    assert a == b


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
        "claims": [
            {
                "id": str(uuid.uuid4()),
                "text": "x",
                "epistemic_type": "fact",
                "semantic_domains": ["other"],
                "source_strength": "unknown",
                "derived_role": "factual",
            }
        ],
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
    assert r.json()["claims"][0]["epistemic_type"] == "fact"
