"""N4.0 internal NewsArticle read API — auth, cursor, filters, list/detail."""
from __future__ import annotations

import base64
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.news import article_read_service as ars  # noqa: E402

ORG_A = "11111111-1111-1111-1111-111111111111"
SOURCE_A = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SOURCE_B = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


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


def _member_claims():
    return {**_admin_claims(), "org_role": "org:member"}


def _row(
    *,
    article_id: uuid.UUID | None = None,
    source_id: uuid.UUID = SOURCE_A,
    guid: str = "g1",
    title: str = "Title",
    url: str = "https://example.com/a",
    summary: str | None = "sum",
    image_url: str | None = None,
    published_at: datetime | None = T0,
    category: str = "tech",
    created_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=article_id or uuid.uuid4(),
        source_id=source_id,
        guid=guid,
        title=title,
        url=url,
        summary=summary,
        image_url=image_url,
        published_at=published_at,
        category=category,
        created_at=created_at or T0,
    )


def _sort_contract(rows: list[SimpleNamespace]) -> list[SimpleNamespace]:
    dated = [r for r in rows if r.published_at is not None]
    undated = [r for r in rows if r.published_at is None]
    dated.sort(key=lambda r: (r.published_at, r.id), reverse=True)
    undated.sort(key=lambda r: r.id, reverse=True)
    return dated + undated


def _after_cursor(
    rows: list[SimpleNamespace],
    published_at: datetime | None,
    article_id: uuid.UUID,
) -> list[SimpleNamespace]:
    out: list[SimpleNamespace] = []
    for r in rows:
        if published_at is not None:
            if r.published_at is not None:
                if r.published_at < published_at or (
                    r.published_at == published_at and r.id < article_id
                ):
                    out.append(r)
            else:
                out.append(r)
        elif r.published_at is None and r.id < article_id:
            out.append(r)
    return out


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self


    def all(self):
        return list(self._rows)


def _session_returning(rows):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Result(rows))
    session.get = AsyncMock(return_value=rows[0] if rows else None)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# --- architecture ---


def test_article_read_service_has_no_network_or_acquisition_imports():
    src = Path(ars.__file__).read_text(encoding="utf-8")
    for banned in (
        "import httpx",
        "import feedparser",
        "from services.acquisition.fetch_safe",
        "import services.acquisition.fetch_safe",
        "collect_source",
        "persist_normalized",
    ):
        assert banned not in src


def test_article_read_service_has_no_migration_side_effects():
    """N4 article read path does not own schema migrations."""
    root = Path(__file__).resolve().parents[1]
    src = (root / "services/news/article_read_service.py").read_text(encoding="utf-8")
    assert "alembic" not in src.lower()
    assert (root / "database" / "migrations" / "versions" / "006_news_v0_1.py").is_file()


# --- cursor unit tests ---


def test_encode_decode_roundtrip_with_filters():
    aid = uuid.uuid4()
    filters = {"source_id": str(SOURCE_A), "category": "tech"}
    token = ars.encode_cursor(published_at=T0, article_id=aid, filters=filters)
    # no padding
    assert "=" not in token
    p, i = ars.decode_cursor(token, request_filters=filters)
    assert p == T0
    assert i == aid


def test_encode_decode_null_published_at():
    aid = uuid.uuid4()
    token = ars.encode_cursor(published_at=None, article_id=aid, filters={})
    p, i = ars.decode_cursor(token, request_filters={})
    assert p is None
    assert i == aid


def test_malformed_cursor_base64():
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor("!!!not-b64!!!", request_filters={})
    assert ei.value.status_code == 422
    assert ei.value.detail == "invalid_cursor"


def test_malformed_cursor_json():
    raw = base64.urlsafe_b64encode(b"not-json").decode("ascii").rstrip("=")
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor(raw, request_filters={})
    assert ei.value.detail == "invalid_cursor"


def test_cursor_missing_fields():
    raw = base64.urlsafe_b64encode(json.dumps({"v": 1, "p": None}).encode()).decode().rstrip("=")
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor(raw, request_filters={})
    assert ei.value.detail == "invalid_cursor"


def test_cursor_wrong_field_types():
    raw = (
        base64.urlsafe_b64encode(
            json.dumps({"v": 1, "p": 123, "i": str(uuid.uuid4()), "f": {}}).encode()
        )
        .decode()
        .rstrip("=")
    )
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor(raw, request_filters={})
    assert ei.value.detail == "invalid_cursor"


def test_cursor_invalid_uuid():
    raw = (
        base64.urlsafe_b64encode(
            json.dumps({"v": 1, "p": None, "i": "not-a-uuid", "f": {}}).encode()
        )
        .decode()
        .rstrip("=")
    )
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor(raw, request_filters={})
    assert ei.value.detail == "invalid_cursor"


def test_cursor_invalid_datetime():
    raw = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"v": 1, "p": "not-a-date", "i": str(uuid.uuid4()), "f": {}}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor(raw, request_filters={})
    assert ei.value.detail == "invalid_cursor"


def test_unsupported_cursor_version():
    raw = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"v": 99, "p": None, "i": str(uuid.uuid4()), "f": {}}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor(raw, request_filters={})
    assert ei.value.detail == "unsupported_cursor_version"


def test_cursor_filter_mismatch():
    token = ars.encode_cursor(
        published_at=T0,
        article_id=uuid.uuid4(),
        filters={"source_id": str(SOURCE_A)},
    )
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor(token, request_filters={"source_id": str(SOURCE_B)})
    assert ei.value.detail == "cursor_filter_mismatch"


def test_category_empty_after_strip_omitted():
    assert ars.normalize_category_filter("   ") is None
    assert ars.normalize_category_filter(None) is None
    assert ars.normalize_category_filter("  tech  ") == "tech"


def test_category_too_long():
    with pytest.raises(HTTPException) as ei:
        ars.normalize_category_filter("x" * 65)
    assert ei.value.status_code == 422


# --- in-memory keyset / ordering contract ---


def test_ordering_and_multipage_keyset_semantics():
    id_hi = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    id_lo = uuid.UUID("00000000-0000-0000-0000-000000000001")
    id_mid = uuid.UUID("88888888-8888-8888-8888-888888888888")
    id_null_hi = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    id_null_lo = uuid.UUID("11111111-1111-1111-1111-111111111111")
    rows = _sort_contract(
        [
            _row(article_id=id_lo, published_at=T0, guid="same-t"),
            _row(article_id=id_hi, published_at=T0, guid="same-t-hi"),
            _row(article_id=id_mid, published_at=T0 + timedelta(hours=1), guid="newer"),
            _row(article_id=id_null_hi, published_at=None, guid="n1"),
            _row(article_id=id_null_lo, published_at=None, guid="n2"),
        ]
    )
    assert [r.guid for r in rows] == ["newer", "same-t-hi", "same-t", "n1", "n2"]

    # multi-page traversal limit=2
    seen: list[str] = []
    cursor_p = None
    cursor_i = None
    filters: dict[str, str] = {}
    pages = 0
    while True:
        pages += 1
        pool = rows if cursor_i is None else _after_cursor(rows, cursor_p, cursor_i)
        page = pool[:2]
        assert page
        seen.extend(r.guid for r in page)
        if len(pool) <= 2:
            break
        last = page[-1]
        token = ars.encode_cursor(
            published_at=last.published_at, article_id=last.id, filters=filters
        )
        cursor_p, cursor_i = ars.decode_cursor(token, request_filters=filters)
    assert seen == ["newer", "same-t-hi", "same-t", "n1", "n2"]
    assert len(seen) == len(set(seen))
    assert pages == 3


# --- service list/detail with mocked DB ---


@pytest.mark.asyncio
async def test_list_empty():
    session = _session_returning([])
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        out = await ars.list_articles()
    assert out["items"] == []
    assert out["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_one_item_shape_and_default_limit():
    row = _row(
        article_id=uuid.UUID("12345678-1234-1234-1234-1234567890ab"),
        image_url="https://example.com/i.png",
    )
    session = _session_returning([row])
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        out = await ars.list_articles()
    assert out["next_cursor"] is None
    assert len(out["items"]) == 1
    item = out["items"][0]
    assert set(item.keys()) == set(ars.ARTICLE_RESPONSE_FIELDS)
    assert item["id"] == str(row.id)
    assert item["source_id"] == str(SOURCE_A)
    assert item["guid"] == "g1"
    assert item["title"] == "Title"
    assert item["url"] == "https://example.com/a"
    assert item["summary"] == "sum"
    assert item["image_url"] == "https://example.com/i.png"
    assert item["published_at"] == T0.isoformat()
    assert item["category"] == "tech"
    assert item["created_at"] == T0.isoformat()
    compiled = str(session.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 21" in compiled.upper().replace("\n", " ")


@pytest.mark.asyncio
async def test_list_limit_plus_one_mints_cursor():
    rows = [
        _row(article_id=uuid.uuid4(), published_at=T0 + timedelta(hours=2), guid="a"),
        _row(article_id=uuid.uuid4(), published_at=T0 + timedelta(hours=1), guid="b"),
        _row(article_id=uuid.uuid4(), published_at=T0, guid="c"),
    ]
    session = _session_returning(rows)
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        out = await ars.list_articles(limit=2)
    assert [i["guid"] for i in out["items"]] == ["a", "b"]
    assert out["next_cursor"] is not None
    p, i = ars.decode_cursor(out["next_cursor"], request_filters={})
    assert p == rows[1].published_at
    assert i == rows[1].id


@pytest.mark.asyncio
async def test_list_final_page_no_cursor():
    rows = [_row(guid="only")]
    session = _session_returning(rows)
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        out = await ars.list_articles(limit=2)
    assert out["next_cursor"] is None


@pytest.mark.asyncio
async def test_stale_cursor_returns_empty_not_error():
    # DB returns nothing for an old cursor — not an error
    token = ars.encode_cursor(published_at=T0, article_id=uuid.uuid4(), filters={})
    session = _session_returning([])
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        out = await ars.list_articles(cursor=token)
    assert out["items"] == []
    assert out["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_filters_applied_and_cursor_reuse():
    rows = [_row(guid="x", category="tech", source_id=SOURCE_A)]
    session = _session_returning(rows)
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        out = await ars.list_articles(
            limit=1,
            source_id=SOURCE_A,
            category=" tech ",
        )
    # only one row returned from mock → no next; mint cursor with filters via encode path
    token = ars.encode_cursor(
        published_at=rows[0].published_at,
        article_id=rows[0].id,
        filters={"source_id": str(SOURCE_A), "category": "tech"},
    )
    p, i = ars.decode_cursor(
        token,
        request_filters={"source_id": str(SOURCE_A), "category": "tech"},
    )
    assert p == rows[0].published_at
    assert i == rows[0].id
    with pytest.raises(HTTPException) as ei:
        ars.decode_cursor(
            token,
            request_filters={"source_id": str(SOURCE_B), "category": "tech"},
        )
    assert ei.value.detail == "cursor_filter_mismatch"
    with pytest.raises(HTTPException) as ei2:
        ars.decode_cursor(
            token,
            request_filters={"source_id": str(SOURCE_A), "category": "world"},
        )
    assert ei2.value.detail == "cursor_filter_mismatch"
    assert out["items"][0]["guid"] == "x"


@pytest.mark.asyncio
async def test_list_db_failure_safe_500():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("password=secret db=prod"))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        with pytest.raises(HTTPException) as ei:
            await ars.list_articles()
    assert ei.value.status_code == 500
    assert "secret" not in str(ei.value.detail)
    assert "prod" not in str(ei.value.detail)


@pytest.mark.asyncio
async def test_get_article_ok():
    row = _row(article_id=uuid.UUID("12345678-1234-1234-1234-1234567890ab"))
    session = _session_returning([row])
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        out = await ars.get_article(row.id)
    assert out["id"] == str(row.id)
    assert out["title"] == "Title"
    session.get.assert_awaited()


@pytest.mark.asyncio
async def test_get_article_missing():
    session = _session_returning([])
    session.get = AsyncMock(return_value=None)
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        with pytest.raises(HTTPException) as ei:
            await ars.get_article(uuid.uuid4())
    assert ei.value.status_code == 404
    assert ei.value.detail == "News article not found"


@pytest.mark.asyncio
async def test_get_article_ignores_source_enabled_state():
    """Detail loads by article PK only — no join on source.enabled."""
    row = _row()
    session = _session_returning([row])
    with patch("services.news.article_read_service.get_db_session", return_value=session):
        out = await ars.get_article(row.id)
    assert out["id"] == str(row.id)
    # get_article must not query NewsSource
    src = Path(ars.__file__).read_text(encoding="utf-8")
    assert "NewsSource" not in src


# --- HTTP auth + wiring ---


def test_list_requires_auth():
    client = TestClient(main.app)
    res = client.get("/api/internal/news/articles")
    assert res.status_code == 401


def test_detail_requires_auth():
    client = TestClient(main.app)
    res = client.get(f"/api/internal/news/articles/{uuid.uuid4()}")
    assert res.status_code == 401


def test_list_forbidden_for_member():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/articles",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 403


def test_detail_forbidden_for_member():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _member_claims(), True),
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/internal/news/articles/{uuid.uuid4()}",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 403


def test_list_ok_for_admin():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.article_read_service.list_articles",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None},
    ) as mocked:
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/articles",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    assert res.json() == {"items": [], "next_cursor": None}
    mocked.assert_awaited()


def test_detail_ok_for_admin():
    aid = uuid.uuid4()
    payload = {
        "id": str(aid),
        "source_id": str(SOURCE_A),
        "guid": "g",
        "title": "T",
        "url": "https://example.com/t",
        "summary": None,
        "image_url": None,
        "published_at": T0.isoformat(),
        "category": "tech",
        "created_at": T0.isoformat(),
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.article_read_service.get_article",
        new_callable=AsyncMock,
        return_value=payload,
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/internal/news/articles/{aid}",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    assert res.json()["id"] == str(aid)


def test_list_limit_bounds_via_framework():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "routers.news_sources.article_read_service.list_articles",
        new_callable=AsyncMock,
        return_value={"items": [], "next_cursor": None},
    ) as mocked:
        client = TestClient(main.app)
        headers = {"Authorization": "Bearer t"}
        assert client.get("/api/internal/news/articles?limit=0", headers=headers).status_code == 422
        assert client.get("/api/internal/news/articles?limit=101", headers=headers).status_code == 422
        assert client.get("/api/internal/news/articles?limit=1", headers=headers).status_code == 200
        assert client.get("/api/internal/news/articles?limit=100", headers=headers).status_code == 200
        assert mocked.await_count == 2
        assert mocked.await_args_list[0].kwargs.get("limit") == 1 or mocked.await_args_list[0].args == ()
        # Query params are passed as kwargs from router
        limits = [
            (c.kwargs.get("limit") if c.kwargs else None)
            for c in mocked.await_args_list
        ]
        assert 1 in limits and 100 in limits


def test_list_category_max_length_via_framework():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/articles",
            params={"category": "x" * 65},
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 422


def test_detail_malformed_uuid():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/articles/not-a-uuid",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 422


def test_list_malformed_cursor_http():
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/articles",
            params={"cursor": "%%%"},
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 422
    assert res.json()["detail"] == "invalid_cursor"


def test_openapi_registers_article_routes():
    client = TestClient(main.app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/internal/news/articles" in paths
    assert "get" in paths["/api/internal/news/articles"]
    assert "/api/internal/news/articles/{article_id}" in paths
    assert "get" in paths["/api/internal/news/articles/{article_id}"]
