"""Phase 2 — non-blocking workspace context wiring in HTTP handlers."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from auth.tenant_binding import TenantContext
from database.thread_store import init_thread_store, upsert_thread_metadata
from services.global_service_store import connect_global_channel, init_global_service_schema
from services.ops.idempotency import reset_idempotency_registry_for_tests
from services.ops.load_governance import reset_load_governor_for_tests
from services.ops.runtime_diagnostics import (
    attach_execution_plan_to_request_diagnostics,
    attach_workspace_to_request_diagnostics,
    begin_request_diagnostics,
    get_request_diagnostics,
)
from services.workspace_resolver import derive_workspace_id_from_slug, resolve_workspace_context

TENANT = "00000000-0000-0000-0000-000000000001"
THREAD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", TENANT)
    reset_idempotency_registry_for_tests()
    reset_load_governor_for_tests()
    yield


@pytest.fixture
def workspace_env(tmp_path, monkeypatch):
    system_db = tmp_path / "system_main.db"
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    init_global_service_schema()
    init_thread_store()
    connect_global_channel(
        TENANT,
        name="Claude",
        source_type="external_library",
        source_metadata={"catalog_key": "engine-claude"},
    )
    return {"system_db": system_db}


@pytest.fixture
def client():
    return TestClient(main.app)


def _tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=TENANT,
        tenant_type="anonymous",
        user_id=None,
        org_id=None,
        org_role=None,
        email=None,
        auth_source="anonymous",
        auth_present=False,
        org_bound=False,
    )


def test_attach_workspace_diagnostics_records_standalone():
    begin_request_diagnostics(route="/test", ctx=_tenant_ctx(), text_hint="hello")
    workspace_ctx = resolve_workspace_context(_tenant_ctx())
    attach_workspace_to_request_diagnostics(workspace_ctx)

    diag = get_request_diagnostics()
    assert diag is not None
    assert diag.workspace_type == "standalone"
    assert diag.workspace_id is not None
    assert diag.workspace_resolution_source == "none"
    assert diag.workspace_membership_verified is False


def test_chat_resolves_workspace_when_thread_has_project_slug(client, workspace_env):
    upsert_thread_metadata(
        thread_id=THREAD_ID,
        org_id=TENANT,
        title="Project workspace",
        session_type="project_setup",
        project_slug="alpha-site",
    )
    expected_workspace_id = derive_workspace_id_from_slug(TENANT, "alpha-site")

    async def fake_chat(message, user_id, tenant_id, tier, *, thread_id=None, provider_id=None, model_override=None, preferred_language=None):
        return {
            "thread_id": str(thread_id or THREAD_ID),
            "response": "ok",
            "model_used": "m",
            "cost_usd": 0.0,
        }

    with patch.object(main, "handle_chat", side_effect=fake_chat):
        with patch.object(main, "attach_workspace_to_request_diagnostics") as attach_mock:
            response = client.post(
                "/chat",
                json={
                    "message": "hi",
                    "tier": "free",
                    "provider_id": "claude",
                    "thread_id": THREAD_ID,
                },
            )

    assert response.status_code == 200
    attach_mock.assert_called_once()
    workspace_ctx = attach_mock.call_args[0][0]
    assert workspace_ctx.workspace_type == "project"
    assert workspace_ctx.project_slug == "alpha-site"
    assert workspace_ctx.workspace_id == expected_workspace_id
    assert workspace_ctx.membership_verified is True


def test_chat_standalone_remains_functional(client, workspace_env):
    with patch.object(
        main,
        "handle_chat",
        new_callable=AsyncMock,
        return_value={
            "thread_id": str(uuid.uuid4()),
            "response": "ok",
            "model_used": "m",
            "cost_usd": 0.0,
        },
    ):
        response = client.post(
            "/chat",
            json={"message": "hi", "tier": "free", "provider_id": "claude"},
        )

    assert response.status_code == 200
    assert response.status_code not in (403, 422)


def test_council_attaches_standalone_workspace_context(client, workspace_env):
    council_payload = {
        "question": "Should we expand?",
        "mode": "copy_paste",
        "response": "Yes.",
        "council": [],
        "synthesis": None,
        "cost_usd": 0.0,
    }

    with patch.object(main, "run_council", new_callable=AsyncMock, return_value=council_payload):
        with patch.object(main, "attach_workspace_to_request_diagnostics") as attach_mock:
            with patch.object(main, "attach_execution_plan_to_request_diagnostics") as plan_mock:
                response = client.post("/council", json={"question": "Should we expand?"})

    assert response.status_code == 200
    attach_mock.assert_called_once()
    plan_mock.assert_called_once()
    workspace_ctx = attach_mock.call_args[0][0]
    plan = plan_mock.call_args[0][0]
    assert workspace_ctx.workspace_type == "standalone"
    assert workspace_ctx.workspace_id is not None
    assert plan.enforcement_owner == "execution_plan"
    assert plan.allowed is True
    assert response.status_code not in (403, 422)


def test_chat_attaches_execution_plan_ownership_diagnostics(client, workspace_env):
    with patch.object(
        main,
        "handle_chat",
        new_callable=AsyncMock,
        return_value={
            "thread_id": str(uuid.uuid4()),
            "response": "ok",
            "model_used": "m",
            "cost_usd": 0.0,
        },
    ):
        with patch.object(main, "attach_execution_plan_to_request_diagnostics") as plan_mock:
            response = client.post(
                "/chat",
                json={"message": "hi", "tier": "free", "provider_id": "claude"},
            )

    assert response.status_code == 200
    plan_mock.assert_called_once()
    plan = plan_mock.call_args[0][0]
    assert plan.enforcement_owner == "execution_plan"
    assert plan.requested_capability == "claude"


@pytest.mark.asyncio
async def test_project_setup_stream_attaches_workspace(workspace_env, monkeypatch):
    from database.thread_store import upsert_thread_metadata
    from services.chat_service import stream_chat_response

    upsert_thread_metadata(
        thread_id=THREAD_ID,
        org_id=TENANT,
        title="Setup",
        session_type="project_setup",
        project_slug="gamma-site",
    )

    org = uuid.UUID(TENANT)
    tid = uuid.UUID(THREAD_ID)

    async def fake_agent(**kwargs):
        yield '{"type":"done","response":"ready","model_used":"gpt-4o-mini","provider_id":"gpt"}\n'

    with patch(
        "services.chat_service.stream_project_agent_response",
        side_effect=lambda **kwargs: fake_agent(),
    ):
        with patch(
            "services.chat_service.resolve_thread_id",
            new=AsyncMock(return_value=tid),
        ):
            with patch(
                "services.chat_service._load_chat_history_messages",
                new=AsyncMock(return_value=[]),
            ):
                with patch(
                    "services.chat_service.attach_workspace_to_request_diagnostics"
                ) as attach_mock:
                    begin_request_diagnostics(route="/chat/stream", ctx=_tenant_ctx(), text_hint="bootstrap")
                    events = []
                    async for line in stream_chat_response(
                        "bootstrap",
                        "anonymous",
                        TENANT,
                        "free",
                        thread_id=tid,
                        provider_id="gpt",
                        project_setup_bootstrap=True,
                    ):
                        events.append(line)

    assert events
    attach_mock.assert_called_once()
    workspace_ctx = attach_mock.call_args[0][0]
    assert workspace_ctx.project_slug == "gamma-site"
    assert workspace_ctx.workspace_id == derive_workspace_id_from_slug(TENANT, "gamma-site")
