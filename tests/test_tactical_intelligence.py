"""Government intelligence and tactical copilot tools."""
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

from services.government_intelligence_service import lookup_site_intelligence, verify_member_compliance
from services.model_gateway import NATIVE_TOOL_NAMES
from services.project_copilot_tools import attach_mutated_state

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_registry_includes_tactical_tools():
    expected = {
        "fetch_site_intelligence",
        "initiate_tactical_quotation",
        "onboard_project_member",
        "log_daily_operations",
    }
    assert expected.issubset(NATIVE_TOOL_NAMES)
    assert len(NATIVE_TOOL_NAMES) >= 11


def test_shoham_site_intelligence_has_scaffolding_history():
    intel = lookup_site_intelligence(site_address="Shoham")
    assert intel["registered_site_manager"] == "Yossi Cohen"
    assert intel["crane_status"] == "active_permit"
    assert "historical_scaffolding_violations" in intel["red_flags"]
    assert intel["safety_premium_pct"] > 0
    assert len(intel["hazard_map"]) >= 1


def test_compliance_blocks_missing_insurance():
    result = verify_member_compliance(name="Test Worker", contract_valid_until="2026-12-31")
    assert result["blocked"] is True
    assert "no_active_insurance" in result["red_flags"]


def test_compliance_passes_valid_worker():
    result = verify_member_compliance(
        name="Valid Worker",
        insurance_policy_id="POL-2026-ACTIVE",
        contract_valid_until="2026-12-31",
        safety_profile_score=85,
    )
    assert result["blocked"] is False
    assert result["compliance_valid"] is True


def test_mutated_state_government_intelligence_card():
    wrapped = attach_mutated_state("fetch_site_intelligence", {"tool": "fetch_site_intelligence"})
    assert wrapped["mutated_state"]["card_type"] == "government_intelligence"


@pytest.mark.asyncio
async def test_fetch_site_intelligence_persists_memory():
    from services.tactical_copilot_tools import fetch_site_intelligence

    with patch("services.tactical_copilot_tools.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.tactical_copilot_tools.save_project_memory", new_callable=AsyncMock
    ) as save_m:
        load_m.return_value = {}
        result = await fetch_site_intelligence(ORG, PROJECT, site_address="Shoham")
    assert result["mutated_state"]["card_type"] == "government_intelligence"
    assert result["registered_site_manager"] == "Yossi Cohen"
    save_m.assert_awaited()


@pytest.mark.asyncio
async def test_onboard_blocks_invalid_compliance():
    from services.tactical_copilot_tools import onboard_project_member

    with patch("services.tactical_copilot_tools.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.tactical_copilot_tools.save_project_memory", new_callable=AsyncMock
    ), patch("services.tactical_copilot_tools.add_project_member", new_callable=AsyncMock) as add_m:
        load_m.return_value = {}
        result = await onboard_project_member(
            ORG,
            PROJECT,
            name="Noncompliant Worker",
            insurance_policy_id="",
            contract_valid_until="2025-01-01",
            safety_profile_score=40,
        )
    assert result["blocked"] is True
    assert result["mutated_state"]["card_type"] == "compliance_insurance"
    add_m.assert_not_awaited()


@pytest.mark.asyncio
async def test_copilot_intel_trigger():
    from services.copilot_orchestrator import run_copilot_preamble

    fake = {
        "mutated_state": {"card_type": "government_intelligence", "payload": {"registered_site_manager": "X"}},
    }
    with patch(
        "services.copilot_orchestrator.fetch_site_intelligence",
        new_callable=AsyncMock,
        return_value=fake,
    ), patch(
        "services.copilot_orchestrator.apply_ambient_memory_from_message",
        new_callable=AsyncMock,
        return_value=None,
    ):
        events = await run_copilot_preamble("@intel site in Shoham", ORG, PROJECT)
    assert any(e.get("card_type") == "government_intelligence" for e in events)
