"""Upskilling requirements, training ROI, and proctor session coordination."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from services.model_gateway import (
    NATIVE_TOOL_NAMES,
    define_tactical_job_requirements,
    simulate_training_day_roi,
)
from services.upskilling_service import (
    STATUTORY_ASSET,
    TRAINABLE_ORIENTATION,
    derive_job_requirements,
    scan_certification_gaps,
    simulate_training_roi,
)

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_registry_includes_upskilling_tools():
    assert "define_tactical_job_requirements" in NATIVE_TOOL_NAMES
    assert "simulate_training_day_roi" in NATIVE_TOOL_NAMES


def test_derive_job_requirements_splits_statutory_and_trainable():
    scope = "electrical fit-out with welding at height on construction site"
    req = derive_job_requirements(scope)
    categories = {s["category"] for s in req["skill_blueprint"]}
    assert STATUTORY_ASSET in categories
    assert TRAINABLE_ORIENTATION in categories
    statutory_ids = {s["skill_id"] for s in req["statutory_assets"]}
    assert "licensed_electrician" in statutory_ids
    trainable_ids = {s["skill_id"] for s in req["trainable_orientations"]}
    assert "certified_height_work" in trainable_ids
    assert "welding_safety_layer" in trainable_ids


def test_scan_certification_gaps_detects_missing_and_expired():
    blueprint = derive_job_requirements("welding at height site work")["skill_blueprint"]
    gaps = scan_certification_gaps(
        skill_blueprint=blueprint,
        member_compliance={"Avi Cohen": {"blocked": False}},
        cert_registry={
            "Avi Cohen": [{"skill_id": "welding_safety_layer", "status": "expired"}],
            "Dana Levi": [],
        },
        project_members=["Avi Cohen", "Dana Levi"],
    )
    assert any(g["worker_name"] == "Avi Cohen" and g["cert_status"] == "expired" for g in gaps)
    assert any(g["worker_name"] == "Dana Levi" and g["cert_status"] == "missing" for g in gaps)


def test_simulate_training_roi_prefers_onsite_for_many_workers():
    gaps = [
        {"worker_name": f"Worker {i}", "skill_id": "x", "skill_label": "Height", "category": "trainable_orientation", "cert_status": "missing"}
        for i in range(5)
    ]
    roi = simulate_training_roi(gaps=gaps, transit_per_worker_nis=120)
    assert roi["affected_worker_count"] == 5
    assert roi["recommended_path"] in ("onsite_proctor", "offsite_individual")
    assert roi["margin_impact"]["training_investment_nis"] > 0


@pytest.mark.asyncio
async def test_define_tactical_job_requirements_returns_upskilling_card():
    with patch("services.model_gateway.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.model_gateway.save_project_memory", new_callable=AsyncMock
    ):
        load_m.return_value = {}
        result = await define_tactical_job_requirements(
            ORG,
            PROJECT,
            engineering_scope="crane lift electrical panel installation at height",
        )
    assert result["mutated_state"]["card_type"] == "upskilling_strategy"
    assert result["statutory_count"] >= 1
    assert result["trainable_count"] >= 1
    assert result["home_base"]


@pytest.mark.asyncio
async def test_simulate_training_day_roi_scan_and_schedule():
    fake_ledger = {"id": "ledger-training-1", "amount": 4800, "entry_type": "EXPENSE", "status": "pending"}
    blueprint = derive_job_requirements("welding electrical site height")["skill_blueprint"]

    with patch("services.model_gateway.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.model_gateway.save_project_memory", new_callable=AsyncMock
    ), patch("services.model_gateway.list_project_members", new_callable=AsyncMock) as list_members, patch(
        "services.model_gateway.create_ledger_entry", new_callable=AsyncMock, return_value=fake_ledger
    ):
        load_m.return_value = {
            "skill_blueprint": blueprint,
            "engineering_scope": "welding electrical site height",
            "member_compliance": {},
            "worker_certifications": {},
            "location_logistics": {"targets": {"Site A": {"fuel_nis": 140}}},
        }
        list_members.return_value = {"members": [{"name": "Avi Cohen"}, {"name": "Dana Levi"}]}
        result = await simulate_training_day_roi(
            ORG,
            PROJECT,
            action="schedule_proctor_session",
            scheduled_date="2026-06-15",
        )

    assert result["mutated_state"]["card_type"] == "onsite_proctor_session"
    assert result["action"] == "schedule_proctor_session"
    assert len(result["invitation_list"]) >= 1
    assert result["ledger_entry"]["id"] == "ledger-training-1"
    assert result["proctor_session"]["scheduled_date"] == "2026-06-15"
    assert result["margin_impact"]["net_after_training_nis"] is not None
