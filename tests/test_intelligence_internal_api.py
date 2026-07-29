"""Internal Intelligence understanding API surface."""
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
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

import main  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"
EVENT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "true")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")


def _admin_claims():
    return {
        "user_id": "user_admin",
        "email": "admin@example.com",
        "org_id": ORG,
        "org_role": "org:admin",
    }


def test_understanding_requires_auth():
    client = TestClient(main.app)
    assert client.get(f"/api/internal/intelligence/events/{EVENT}/understanding").status_code == 401


def test_understanding_get_ok():
    detail = {
        "understanding": {
            "event_id": str(EVENT),
            "package_version": 1,
            "primary_event_type": "acquisition",
            "classifier_version": "event_classifier_v1",
            "template_version": "question_templates_v1",
        }
    }
    with patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", _admin_claims(), True),
    ), patch(
        "services.intelligence.persistence.get_event_understanding",
        new_callable=AsyncMock,
        return_value=detail,
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/internal/intelligence/events/{EVENT}/understanding",
            headers={"Authorization": "Bearer t"},
            params={"package_version": 1},
        )
    assert res.status_code == 200
    assert res.json()["understanding"]["primary_event_type"] == "acquisition"


def test_openapi_includes_intelligence_routes():
    client = TestClient(main.app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/internal/intelligence/events/{event_id}/understanding" in paths
    assert "/api/internal/intelligence/events/{event_id}/understanding/materialize" in paths
