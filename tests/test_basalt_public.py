"""Basalt public corporate API and application review pipeline."""
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

import main
from services.basalt_content_schema import build_corporate_content
from services.basalt_public_service import parse_resume_skills, resolve_basalt_org_id
from services.model_gateway import NATIVE_TOOL_NAMES, review_basalt_application
from services.attendance_service import compute_daily_food_allowance_nis, FLAG_LATE_ARRIVAL

ORG = uuid.UUID("22222222-2222-2222-2222-222222222222")
PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture(autouse=True)
def _basalt_env(monkeypatch):
    monkeypatch.setenv("BASALT_ORG_ID", str(ORG))
    monkeypatch.setenv("BASALT_DEFAULT_PROJECT_ID", str(PROJECT))
    monkeypatch.setenv("BASALT_PUBLIC_RATE_LIMIT", "1000")


def test_corporate_content_includes_ehs_and_cost_engineering():
    en = build_corporate_content("en")
    he = build_corporate_content("he")
    assert "Data Center Infrastructure" in en["hero"]
    assert "דאטה סנטר" in he["hero"]
    assert "Zero Friction" in en["ehs_compliance"]["headline"]
    assert "Cost Engineering" in en["cost_engineering"]["headline"]
    assert en["cost_engineering"]["home_base"] == "Or Akiva"


def test_parse_resume_skills_detects_welder_and_draftsman():
    text = "Experienced welder and draftsman with autocad on data center projects"
    skills = parse_resume_skills(text)
    roles = {s["role"] for s in skills}
    assert "Welder" in roles
    assert "Draftsman" in roles


def test_registry_includes_review_basalt_application():
    assert "review_basalt_application" in NATIVE_TOOL_NAMES


def test_public_jobs_endpoint_returns_openings():
    client = TestClient(main.app)
    with patch(
        "routers.public_basalt.fetch_active_job_openings",
        new_callable=AsyncMock,
        return_value={"openings": [{"title": "Electrician"}], "opening_count": 1, "lang": "en"},
    ):
        res = client.get("/api/public/basalt/jobs?lang=en")
    assert res.status_code == 200
    data = res.json()
    assert data["opening_count"] == 1
    assert resolve_basalt_org_id() == ORG


def test_public_apply_stores_pending_review_application():
    client = TestClient(main.app)
    fake_result = {
        "application_id": "app-1",
        "status": "PENDING_REVIEW",
        "skill_matrix": [{"role": "Welder"}],
        "certification_count": 1,
    }
    with patch(
        "routers.public_basalt.submit_candidate_application",
        new_callable=AsyncMock,
        return_value=fake_result,
    ):
        res = client.post(
            "/api/public/basalt/apply",
            json={
                "candidate_name": "Yossi Cohen",
                "resume_text": "certified welder height safety",
                "certifications": [{"cert_type": "height_safety", "filename": "height.pdf"}],
            },
        )
    assert res.status_code == 200
    assert res.json()["status"] == "PENDING_REVIEW"


def test_public_portfolio_endpoint():
    client = TestClient(main.app)
    with patch(
        "routers.public_basalt.fetch_verified_portfolio",
        new_callable=AsyncMock,
        return_value={"portfolio": [{"milestone": "Phase 1"}], "milestone_count": 1},
    ):
        res = client.get("/api/public/basalt/portfolio")
    assert res.status_code == 200
    assert res.json()["milestone_count"] == 1


def test_food_allowance_stays_within_65_100_band():
    low = compute_daily_food_allowance_nis(operational_flags=[FLAG_LATE_ARRIVAL], partial_shift_ratio=0.3)
    assert 65 <= low <= 100


@pytest.mark.asyncio
async def test_review_basalt_inbox_returns_web_application_card():
    pending_app = {
        "id": "app-99",
        "status": "PENDING_REVIEW",
        "candidate_name": "Dana Levi",
        "skill_matrix": [{"role": "Welder"}],
        "pending_flash": True,
    }
    with patch(
        "services.model_gateway.list_pending_applications",
        new_callable=AsyncMock,
        return_value=[pending_app],
    ):
        result = await review_basalt_application(ORG, PROJECT, action="inbox")
    assert result["mutated_state"]["card_type"] == "basalt_web_application"
    assert result["application"]["candidate_name"] == "Dana Levi"


@pytest.mark.asyncio
async def test_review_basalt_approve_onboard():
    pending_app = {
        "id": "app-100",
        "status": "PENDING_REVIEW",
        "candidate_name": "Avi Test",
        "desired_role": "Welder",
        "certifications": [{"cert_type": "height_safety"}],
    }
    with patch(
        "services.model_gateway.list_pending_applications",
        new_callable=AsyncMock,
        return_value=[pending_app],
    ), patch(
        "services.model_gateway.onboard_project_member",
        new_callable=AsyncMock,
        return_value={"worker_name": "Avi Test", "blocked": False},
    ), patch(
        "services.model_gateway.load_project_memory",
        new_callable=AsyncMock,
        return_value={"worker_certifications": {}},
    ), patch(
        "services.model_gateway.save_project_memory",
        new_callable=AsyncMock,
    ), patch(
        "services.model_gateway.mark_application_reviewed",
        new_callable=AsyncMock,
        return_value=pending_app,
    ):
        result = await review_basalt_application(
            ORG, PROJECT, action="approve_onboard", application_id="app-100"
        )
    assert result["action"] == "approve_onboard"
    assert result["onboard_result"]["worker_name"] == "Avi Test"
