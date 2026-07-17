"""N2 News Source Registry — auth, URL validation, and CRUD route wiring."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from auth.news_registry_privileges import (  # noqa: E402
    assert_can_manage_news_sources,
    can_manage_news_sources,
)
from auth.tenant_binding import TenantContext  # noqa: E402
from services.news.feed_url import normalize_and_validate_feed_url, validate_feed_url  # noqa: E402

ORG_A = "11111111-1111-1111-1111-111111111111"
SOURCE_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture(autouse=True)
def _news_registry_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)


def _ctx(
    *,
    tenant_type: str = "organization",
    org_role: str | None = "org:admin",
    auth_source: str = "clerk_jwt",
) -> TenantContext:
    return TenantContext(
        tenant_id=ORG_A,
        tenant_type=tenant_type,  # type: ignore[arg-type]
        user_id="user_1",
        org_id=ORG_A if tenant_type == "organization" else None,
        org_role=org_role,
        email="a@b.com",
        auth_source=auth_source,  # type: ignore[arg-type]
        auth_present=True,
        org_bound=tenant_type == "organization",
    )


def _sample_source(**overrides):
    base = {
        "id": str(SOURCE_ID),
        "name": "Example",
        "feed_url": "https://example.com/feed",
        "category": "tech",
        "language": "en",
        "enabled": True,
        "created_at": "2026-07-17T00:00:00+00:00",
        "updated_at": "2026-07-17T00:00:00+00:00",
        "request_id": "test-rid",
    }
    base.update(overrides)
    return base


# --- feed_url unit tests ---


@pytest.mark.parametrize(
    "raw,ok",
    [
        ("https://example.com/rss.xml", True),
        ("HTTP://Example.COM/Path?q=1", True),
        ("https://example.com:8443/feed", True),
        ("ftp://example.com/feed", False),
        ("https://user:pass@example.com/feed", False),
        ("https://localhost/feed", False),
        ("https://127.0.0.1/feed", False),
        ("https://10.0.0.1/feed", False),
        ("https://192.168.1.1/feed", False),
        ("https://169.254.169.254/latest/meta-data", False),
        ("https://[::1]/feed", False),
        ("https://metadata.google.internal/", False),
        ("not-a-url", False),
        ("", False),
        ("   ", False),
    ],
)
def test_normalize_and_validate_feed_url_cases(raw, ok):
    normalized, errors = normalize_and_validate_feed_url(raw)
    assert (not errors) is ok
    if ok:
        assert normalized is not None
        assert normalized.startswith("http")
        assert "EXAMPLE" not in normalized  # host lowercased
    else:
        assert normalized is None
        assert errors


def test_validate_feed_url_payload_shape():
    good = validate_feed_url("  https://Example.com/feed  ")
    assert good == {
        "valid": True,
        "errors": [],
        "normalized_url": "https://example.com/feed",
    }
    bad = validate_feed_url("https://127.0.0.1/x")
    assert bad["valid"] is False
    assert bad["normalized_url"] is None
    assert bad["errors"]


def test_feed_url_preserves_path_and_query():
    normalized, errors = normalize_and_validate_feed_url(
        "https://News.Example.org/a/b?x=1&y=2#frag"
    )
    assert not errors
    assert normalized == "https://news.example.org/a/b?x=1&y=2"


def test_feed_url_helpers_have_no_network_imports():
    import services.news.feed_url as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    for banned in ("import socket", "import httpx", "urllib.request", "feedparser", "import dns"):
        assert banned not in src


# --- privileges ---


def test_privileges_beta_and_org_admin():
    assert can_manage_news_sources(_ctx(auth_source="beta_passcode", tenant_type="anonymous", org_role=None))
    assert can_manage_news_sources(_ctx(org_role="org:admin"))
    assert can_manage_news_sources(_ctx(org_role="owner"))
    assert not can_manage_news_sources(_ctx(org_role="org:member"))
    assert not can_manage_news_sources(_ctx(tenant_type="personal", org_role=None))
    with pytest.raises(Exception) as ei:
        assert_can_manage_news_sources(_ctx(org_role="member"))
    assert ei.value.status_code == 403


# --- HTTP auth + wiring ---


def test_list_requires_auth():
    client = TestClient(main.app)
    res = client.get("/api/internal/news/sources")
    assert res.status_code == 401


def test_create_requires_auth():
    client = TestClient(main.app)
    res = client.post(
        "/api/internal/news/sources",
        json={
            "name": "A",
            "feed_url": "https://example.com/feed",
            "category": "tech",
        },
    )
    assert res.status_code == 401


def test_personal_tenant_forbidden():
    claims = {"user_id": "user_1", "email": "a@b.com"}  # no org → personal
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/sources",
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 403


def test_org_member_forbidden():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:member",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/sources",
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 403


def test_list_ok_for_org_admin():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ), patch(
        "routers.news_sources.source_registry.list_sources",
        new_callable=AsyncMock,
        return_value={"sources": [_sample_source()], "request_id": "r1"},
    ):
        client = TestClient(main.app)
        res = client.get(
            "/api/internal/news/sources",
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 200
    assert len(res.json()["sources"]) == 1


def test_create_ok_for_org_admin():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ), patch(
        "routers.news_sources.source_registry.create_source",
        new_callable=AsyncMock,
        return_value=_sample_source(),
    ) as create_mock:
        client = TestClient(main.app)
        res = client.post(
            "/api/internal/news/sources",
            json={
                "name": " Example ",
                "feed_url": "https://example.com/feed",
                "category": "tech",
                "language": "en",
                "enabled": True,
            },
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 201
    create_mock.assert_awaited_once()
    kwargs = create_mock.await_args.kwargs
    assert kwargs["name"] == "Example"
    assert kwargs["feed_url"] == "https://example.com/feed"


def test_validate_endpoint():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.post(
            "/api/internal/news/sources/validate",
            json={"feed_url": "https://Example.com/rss"},
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["normalized_url"] == "https://example.com/rss"
    assert body["errors"] == []


def test_validate_rejects_private_ip():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.post(
            "/api/internal/news/sources/validate",
            json={"feed_url": "https://10.1.2.3/feed"},
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 200
    assert res.json()["valid"] is False


def test_empty_patch_rejected():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.patch(
            f"/api/internal/news/sources/{SOURCE_ID}",
            json={},
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 422


def test_enable_disable_and_get():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ), patch(
        "routers.news_sources.source_registry.set_enabled",
        new_callable=AsyncMock,
        return_value=_sample_source(enabled=False),
    ) as en_mock, patch(
        "routers.news_sources.source_registry.get_source",
        new_callable=AsyncMock,
        return_value=_sample_source(),
    ):
        client = TestClient(main.app)
        headers = {"Authorization": "Bearer test-token"}
        assert client.get(f"/api/internal/news/sources/{SOURCE_ID}", headers=headers).status_code == 200
        assert (
            client.post(f"/api/internal/news/sources/{SOURCE_ID}/disable", headers=headers).status_code
            == 200
        )
        assert (
            client.post(f"/api/internal/news/sources/{SOURCE_ID}/enable", headers=headers).status_code
            == 200
        )
    assert en_mock.await_count == 2


def test_create_rejects_extra_fields():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.post(
            "/api/internal/news/sources",
            json={
                "name": "A",
                "feed_url": "https://example.com/feed",
                "category": "tech",
                "priority": 1,
            },
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 422


def test_patch_rejects_enabled_field():
    claims = {
        "user_id": "user_1",
        "email": "a@b.com",
        "org_id": ORG_A,
        "org_role": "org:admin",
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    ):
        client = TestClient(main.app)
        res = client.patch(
            f"/api/internal/news/sources/{SOURCE_ID}",
            json={"enabled": False},
            headers={"Authorization": "Bearer test-token"},
        )
    assert res.status_code == 422
