"""Invoice capture and native tool registry."""
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
from services.invoice_tools import _parse_amount_from_text, _parse_vendor_from_text
from services.model_gateway import NATIVE_TOOL_NAMES, execute_native_tool

EXPECTED_TOOL_COUNT = 11

ORG_A = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")


def _auth_headers():
    return {"Authorization": "Bearer test-token"}


def _patch_valid_jwt():
    claims = {"user_id": "user_1", "email": "a@b.com", "org_id": ORG_A}
    return patch(
        "auth.beta_gate.authenticate_request",
        return_value=("auth_valid", claims, True),
    )


def test_parse_amount_from_filename():
    assert _parse_amount_from_text("Acme_Supplies_120.50.pdf") == 120.50
    assert _parse_amount_from_text("invoice-99.pdf") == 99.0


def test_parse_vendor_from_filename():
    assert _parse_vendor_from_text("Acme_Supplies_120.50.pdf") == "Acme Supplies"
    assert _parse_vendor_from_text("vendor-invoice.png") == "vendor invoice"


def test_native_tool_registry_includes_invoice_tools():
    assert "process_captured_invoice" in NATIVE_TOOL_NAMES
    assert "process_credit_memo" in NATIVE_TOOL_NAMES
    assert "initiate_quotation_flow" in NATIVE_TOOL_NAMES
    assert "get_cash_flow_forecast" in NATIVE_TOOL_NAMES
    assert len(NATIVE_TOOL_NAMES) >= EXPECTED_TOOL_COUNT


@pytest.mark.asyncio
async def test_execute_native_tool_unknown_raises():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await execute_native_tool(
            "nonexistent_tool",
            {},
            org_id=uuid.UUID(ORG_A),
            project_id=uuid.UUID(PROJECT_ID),
        )
    assert exc.value.status_code == 404


def test_invoice_capture_requires_auth():
    client = TestClient(main.app)
    res = client.post(
        f"/api/projects/{PROJECT_ID}/invoices/capture",
        json={"filename": "Acme_50.00.pdf"},
    )
    assert res.status_code == 401


def test_ledger_export_requires_auth():
    client = TestClient(main.app)
    res = client.post(
        f"/api/projects/{PROJECT_ID}/ledger/export",
        json={"format": "summary"},
    )
    assert res.status_code == 401


def test_list_native_tools_requires_auth():
    client = TestClient(main.app)
    res = client.get(f"/api/projects/{PROJECT_ID}/tools")
    assert res.status_code == 401


def test_invoice_capture_delegates_to_service():
    fake_result = {
        "tool": "process_captured_invoice",
        "saved_to_ledger": True,
        "amount": 50.0,
        "currency": "ILS",
        "vendor": "Acme",
        "mutated_state": {"card_type": "receipt_capture", "payload": {}},
    }

    with _patch_valid_jwt(), patch(
        "routers.projects.process_captured_invoice",
        new_callable=AsyncMock,
        return_value=fake_result,
    ):
        client = TestClient(main.app)
        res = client.post(
            f"/api/projects/{PROJECT_ID}/invoices/capture",
            json={"filename": "Acme_50.00.pdf"},
            headers=_auth_headers(),
        )
    assert res.status_code == 200
    assert res.json()["saved_to_ledger"] is True


def test_tools_execute_delegates_to_gateway():
    fake_result = {"tool": "export_ledger_to_accountant", "entry_count": 2}

    with _patch_valid_jwt(), patch(
        "routers.projects.execute_native_tool",
        new_callable=AsyncMock,
        return_value=fake_result,
    ):
        client = TestClient(main.app)
        res = client.post(
            f"/api/projects/{PROJECT_ID}/tools/execute",
            json={"tool_name": "export_ledger_to_accountant", "arguments": {"format": "summary"}},
            headers=_auth_headers(),
        )
    assert res.status_code == 200
    assert res.json()["entry_count"] == 2
