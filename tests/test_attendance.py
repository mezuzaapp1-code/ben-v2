"""Attendance parsing and process_worker_response tool."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from services.attendance_service import (
    FLAG_EARLY_DEPARTURE,
    FLAG_LATE_ARRIVAL,
    FLAG_PARTIAL_SHIFT,
    parse_worker_hours_text,
)
from services.model_gateway import NATIVE_TOOL_NAMES, process_worker_response

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_registry_includes_process_worker_response():
    assert "process_worker_response" in NATIVE_TOOL_NAMES


def test_parse_full_shift_on_time():
    parsed = parse_worker_hours_text("arrived 07:00 left 17:00")
    assert parsed["hours_worked"] == 10.0
    assert parsed["operational_flags"] == []


def test_parse_late_arrival():
    parsed = parse_worker_hours_text("stuck in traffic, arrived 08:30 left 17:00")
    assert FLAG_LATE_ARRIVAL in parsed["operational_flags"]
    assert parsed["hours_worked"] == 8.5
    assert any("traffic" in h for h in parsed["reason_hints"])


def test_parse_early_departure_doctor():
    parsed = parse_worker_hours_text("07:00 - 14:30 left for doctor appointment")
    assert FLAG_EARLY_DEPARTURE in parsed["operational_flags"]
    assert FLAG_PARTIAL_SHIFT in parsed["operational_flags"]
    assert parsed["hours_worked"] == 7.5


def test_parse_decimal_hours_partial():
    parsed = parse_worker_hours_text("partial shift 6.5 hours")
    assert parsed["hours_worked"] == 6.5
    assert FLAG_PARTIAL_SHIFT in parsed["operational_flags"]


@pytest.mark.asyncio
async def test_process_worker_response_creates_time_card():
    with patch("services.model_gateway.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.model_gateway.save_project_memory", new_callable=AsyncMock
    ):
        load_m.return_value = {"subsistence": {"daily_allowance_nis": 80}, "attendance_log": []}
        result = await process_worker_response(
            ORG,
            PROJECT,
            worker_name="Dana",
            response_text="arrived 08:45 left 17:00 traffic delay",
        )
    assert result["mutated_state"]["card_type"] == "daily_attendance_delay"
    card = result["time_card"]
    assert card["worker_name"] == "Dana"
    assert FLAG_LATE_ARRIVAL in card["operational_flags"]
    assert card["status"] == "pending"
    assert card["pay"]["wage_nis"] > 0


@pytest.mark.asyncio
async def test_process_worker_response_approve_with_adjusted_hours():
    card_id = "tc-1"
    existing = {
        "id": card_id,
        "date": "2026-06-06",
        "worker_name": "Dana",
        "status": "pending",
        "hours_worked": 7.5,
        "standard_hours": 10.0,
        "operational_flags": [FLAG_EARLY_DEPARTURE],
        "pay": {"wage_nis": 487.5, "subsistence_nis": 60},
    }
    with patch("services.model_gateway.load_project_memory", new_callable=AsyncMock) as load_m, patch(
        "services.model_gateway.save_project_memory", new_callable=AsyncMock
    ):
        load_m.return_value = {
            "subsistence": {"daily_allowance_nis": 80},
            "attendance_log": [existing],
        }
        result = await process_worker_response(
            ORG,
            PROJECT,
            worker_name="Dana",
            approve=True,
            time_card_id=card_id,
            adjusted_hours=8.0,
        )
    assert result["action"] == "approved"
    assert result["time_card"]["status"] == "approved"
    assert result["time_card"]["hours_worked"] == 8.0


@pytest.mark.asyncio
async def test_process_worker_response_requires_text_when_not_approving():
    with patch("services.model_gateway.load_project_memory", new_callable=AsyncMock) as load_m:
        load_m.return_value = {"subsistence": {}, "attendance_log": []}
        with pytest.raises(HTTPException) as exc:
            await process_worker_response(ORG, PROJECT, worker_name="Dana")
    assert exc.value.status_code == 422
