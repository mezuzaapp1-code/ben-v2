"""Cost Engineering supplier tender analysis."""
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

from services.cost_engineering_service import (
    build_historical_baseline,
    detect_cost_anomalies,
    parse_supplier_bid,
)
from services.model_gateway import NATIVE_TOOL_NAMES, analyze_supplier_tender

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_registry_includes_analyze_supplier_tender():
    assert "analyze_supplier_tender" in NATIVE_TOOL_NAMES


def test_parse_bid_into_cost_matrix_layers():
    bid = (
        "Supplier SteelCo quote: materials 68000, freight 18000, operational 8000, margin 7000. "
        "Total 103000 NIS"
    )
    parsed = parse_supplier_bid(bid, supplier_name="SteelCo")
    cm = parsed["cost_matrix"]
    assert cm["base_material_cost"] > 67000
    assert cm["logistics_freight_overhead"] > 17000
    assert cm["total_bid_nis"] == 103000
    assert sum(cm[k] for k in cm if k not in ("total_bid_nis", "layer_pcts")) == 103000


def test_detect_inflated_freight_anomaly():
    cm = {
        "total_bid_nis": 100000,
        "base_material_cost": 60000,
        "logistics_freight_overhead": 22000,
        "operational_overheads": 10000,
        "supplier_margin_risk_premium": 8000,
        "layer_pcts": {
            "base_material_cost": 60.0,
            "logistics_freight_overhead": 22.0,
            "operational_overheads": 10.0,
            "supplier_margin_risk_premium": 8.0,
        },
    }
    baseline = build_historical_baseline([], [])
    anomalies = detect_cost_anomalies(cm, baseline)
    freight_flags = [a for a in anomalies if a.get("layer") == "logistics_freight_overhead"]
    assert len(freight_flags) >= 1


@pytest.mark.asyncio
async def test_analyze_supplier_tender_returns_tabulation_card():
    with patch("services.model_gateway.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.model_gateway.save_project_memory", new_callable=AsyncMock
    ), patch("services.model_gateway.list_ledger_entries", new_callable=AsyncMock) as list_ledger:
        load_m.return_value = {"procurement_tenders": []}
        list_ledger.return_value = {"entries": [{"entry_type": "EXPENSE", "amount": 50000}]}
        result = await analyze_supplier_tender(
            ORG,
            PROJECT,
            bid_text="from Acme materials 50000 freight 15000 operational 5000 margin 5000 total 75000",
        )
    assert result["mutated_state"]["card_type"] == "cost_engineering_bid_tabulation"
    assert result["tender"]["supplier_name"]
    assert len(result["layers"]) == 4


@pytest.mark.asyncio
async def test_accept_bid_updates_ledger_and_shopping_log():
    tender_id = "t-1"
    existing_tender = {
        "id": tender_id,
        "status": "evaluated",
        "supplier_name": "SteelCo",
        "cost_matrix": {"total_bid_nis": 75000},
        "bid_text": "steel supply",
    }
    fake_ledger = {"id": "ledger-1", "amount": 75000, "entry_type": "EXPENSE"}

    with patch("services.model_gateway.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.model_gateway.save_project_memory", new_callable=AsyncMock
    ), patch("services.model_gateway.create_ledger_entry", new_callable=AsyncMock, return_value=fake_ledger):
        load_m.return_value = {"procurement_tenders": [existing_tender], "daily_operations": [], "shopping_log": []}
        result = await analyze_supplier_tender(
            ORG,
            PROJECT,
            action="accept_bid",
            tender_id=tender_id,
            materials=["rebar_12mm"],
        )
    assert result["action"] == "accept_bid"
    assert result["ledger_entry"]["id"] == "ledger-1"
    assert result["shopping_log_entry"]["materials"] == ["rebar_12mm"]
