"""Project copilot tools, memory matrix, and mutated_state payloads."""
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

from services.model_gateway import NATIVE_TOOL_NAMES
from services.project_copilot_tools import attach_mutated_state
from services.project_memory_service import compute_location_logistics, compute_subsistence_overhead

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_native_tool_registry_includes_copilot_tools():
    expected = {
        "initiate_quotation_flow",
        "process_captured_invoice",
        "process_credit_memo",
        "issue_customer_invoice",
        "get_cash_flow_forecast",
        "get_lifecycle_overview",
        "export_ledger_to_accountant",
        "fetch_site_intelligence",
        "initiate_tactical_quotation",
        "onboard_project_member",
        "log_daily_operations",
    }
    assert expected.issubset(NATIVE_TOOL_NAMES)
    assert len(NATIVE_TOOL_NAMES) >= 11


def test_attach_mutated_state_wraps_quotation():
    raw = {"tool": "initiate_quotation_flow", "active": True}
    wrapped = attach_mutated_state("initiate_quotation_flow", raw)
    assert wrapped["mutated_state"]["card_type"] == "quotation_deliberation"
    assert wrapped["mutated_state"]["payload"] == raw


def test_location_logistics_or_akiva_to_shoham():
    logistics = compute_location_logistics("Shoham")
    assert logistics["base"] == "Or Akiva"
    assert logistics["target"] == "Shoham"
    assert logistics["round_trip_km"] == logistics["distance_km"] * 2
    assert logistics["fuel_nis"] > 0


def test_subsistence_overhead_within_benchmark():
    overhead = compute_subsistence_overhead(active_members=3, task_days=5, daily_allowance_nis=80)
    assert overhead["daily_allowance_nis"] >= 65
    assert overhead["daily_allowance_nis"] <= 100
    assert overhead["total_overhead_nis"] == 80 * 3 * 5


@pytest.mark.asyncio
async def test_initiate_quotation_flow_start_returns_checklist():
    from services.project_copilot_tools import initiate_quotation_flow

    with patch("services.project_copilot_tools.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.project_copilot_tools.save_project_memory", new_callable=AsyncMock
    ) as save_m, patch(
        "services.project_copilot_tools.touch_lifecycle_phase", new_callable=AsyncMock
    ):
        load_m.return_value = {
            "quotation_flow": {
                "active": False,
                "current_step": None,
                "steps": {
                    "location": {"label": "Location", "completed": False, "data": {}},
                    "materials_suppliers": {"label": "Materials / Suppliers", "completed": False, "data": {}},
                    "risk_mitigation": {"label": "Risk Mitigation", "completed": False, "data": {}},
                    "labor_execution": {"label": "Labor / Execution", "completed": False, "data": {}},
                },
            },
            "location_logistics": {"targets": {}},
            "subsistence": {},
            "estimates": {},
        }
        save_m.return_value = load_m.return_value
        result = await initiate_quotation_flow(ORG, PROJECT, action="start")
    assert result["tool"] == "initiate_quotation_flow"
    assert result["mutated_state"]["card_type"] == "quotation_deliberation"
    assert len(result["checklist"]) == 4
    assert result["current_step"] == "location"


@pytest.mark.asyncio
async def test_copilot_preamble_quote_trigger():
    from services.copilot_orchestrator import run_copilot_preamble

    fake_result = {
        "tool": "initiate_quotation_flow",
        "mutated_state": {"card_type": "quotation_deliberation", "payload": {"active": True}},
    }
    with patch(
        "services.copilot_orchestrator.initiate_quotation_flow",
        new_callable=AsyncMock,
        return_value=fake_result,
    ), patch(
        "services.copilot_orchestrator.apply_ambient_memory_from_message",
        new_callable=AsyncMock,
        return_value=None,
    ):
        events = await run_copilot_preamble("@quote start", ORG, PROJECT)
    assert len(events) == 1
    assert events[0]["type"] == "mutated_state"
    assert events[0]["card_type"] == "quotation_deliberation"
