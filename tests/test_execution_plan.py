"""Execution plan resolver — Phase 3 observability + Phase 4 chat enforcement."""
from __future__ import annotations

import pytest

from auth.tenant_binding import TenantContext
from services.execution_plan import (
    ExecutionPlan,
    ExecutionPlanResolver,
    execution_plan_to_log_payload,
    resolve_execution_plan,
    resolve_workspace_intent_enabled,
)
from services.global_service_store import connect_global_channel, init_global_service_schema
from services.ops.runtime_diagnostics import (
    attach_execution_plan_to_request_diagnostics,
    begin_request_diagnostics,
    get_request_diagnostics,
)
from services.workspace_resolver import (
    derive_workspace_id_from_slug,
    resolve_workspace_context,
)

ORG = "00000000-0000-0000-0000-000000000001"

_ACTIVE_ENGINE_CATALOG = (
    ("engine-grok", "Grok Compute Grid"),
    ("engine-claude", "Claude Reasoning Core"),
    ("engine-gemini", "Gemini Multimodal"),
)


@pytest.fixture(autouse=True)
def _seed_switchboard(tmp_path, monkeypatch):
    system_db = tmp_path / "system_main.db"
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    init_global_service_schema()
    for catalog_key, name in _ACTIVE_ENGINE_CATALOG:
        connect_global_channel(
            ORG,
            name=name,
            source_type="external_library",
            source_metadata={"catalog_key": catalog_key},
        )


def _org_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=ORG,
        tenant_type="organization",
        user_id="user-test-1",
        org_id=ORG,
        org_role="admin",
        email="test@example.com",
        auth_source="clerk_jwt",
        auth_present=True,
        org_bound=True,
    )


def _standalone_workspace():
    return resolve_workspace_context(_org_ctx())


def _project_workspace(slug: str = "alpha-site"):
    return resolve_workspace_context(_org_ctx(), project_slug=slug)


def test_standard_chat_plan_creation():
    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="claude")

    assert isinstance(plan, ExecutionPlan)
    assert plan.capability_key == "standard_chat"
    assert plan.org_id == ORG
    assert plan.requested_resource == "claude"
    assert plan.resolved_resource == "claude"
    assert plan.connector_id == "anthropic_adapter"
    assert plan.enforced is True
    assert plan.allowed is True
    assert plan.activation_source == "org_switchboard"


def test_standard_chat_hyphen_capability_key():
    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard-chat", requested_resource="gpt")
    assert plan.enforced is True
    assert plan.allowed is True


def test_council_plan_creation():
    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "council")

    assert plan.capability_key == "council"
    assert plan.requested_resource is None
    assert plan.resolved_resource is None
    assert plan.connector_id is None
    assert plan.enforced is False
    assert plan.allowed is True


def test_standalone_workspace_support():
    workspace = _standalone_workspace()
    assert workspace.workspace_type == "standalone"
    assert workspace.workspace_id is not None
    assert workspace.context_id == workspace.workspace_id

    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="gpt")
    assert plan.workspace_type == "standalone"
    assert plan.workspace_id is not None
    assert plan.allowed is True


def test_project_workspace_support():
    workspace = _project_workspace("beta-site")
    expected_id = derive_workspace_id_from_slug(ORG, "beta-site")

    assert workspace.workspace_type == "project"
    assert workspace.workspace_id == expected_id

    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="gpt-4o")
    assert plan.workspace_id == expected_id
    assert plan.workspace_type == "project"
    assert plan.allowed is True


@pytest.mark.parametrize(
    ("resource", "connector"),
    [
        ("gpt", "openai_adapter"),
        ("gpt-4o-mini", "openai_adapter"),
        ("claude", "anthropic_adapter"),
        ("claude-3-opus", "anthropic_adapter"),
        ("gemini", "google_adapter"),
        ("gemini-2.0-flash", "google_adapter"),
    ],
)
def test_connector_resolution(resource: str, connector: str):
    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource=resource)
    assert plan.connector_id == connector
    assert plan.allowed is True


def test_unknown_resource_handling():
    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="llama-3")

    assert plan.requested_resource == "llama-3"
    assert plan.resolved_resource == "llama-3"
    assert plan.connector_id is None
    assert plan.enforced is False
    assert plan.allowed is True


def test_chat_enforcement_inactive_engine(tmp_path, monkeypatch):
    system_db = tmp_path / "inactive_system_main.db"
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    init_global_service_schema()

    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="claude")

    assert plan.enforced is True
    assert plan.allowed is False
    assert plan.activation_source == "org_switchboard"


def test_chat_enforcement_no_provider_bypass():
    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource=None)

    assert plan.enforced is False
    assert plan.allowed is True
    assert plan.activation_source == "diagnostic_only"


def test_council_enforced_false():
    plan = resolve_execution_plan(_standalone_workspace(), "council")
    assert plan.enforced is False


def test_council_allowed_true():
    plan = resolve_execution_plan(_standalone_workspace(), "council")
    assert plan.allowed is True


def test_no_runtime_exceptions_on_edge_inputs():
    workspace = _standalone_workspace()
    cases = [
        ("standard_chat", None),
        ("standard_chat", ""),
        ("standard_chat", "  gpt  "),
        ("council", None),
    ]
    for capability, resource in cases:
        plan = resolve_execution_plan(workspace, capability, requested_resource=resource)
        assert isinstance(plan, ExecutionPlan)


def test_capability_key_required():
    with pytest.raises(ValueError, match="capability_key"):
        resolve_execution_plan(_standalone_workspace(), "")


def test_execution_plan_to_log_payload():
    workspace = _project_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="gemini")
    payload = execution_plan_to_log_payload(plan)

    assert payload["workspace_id"] == workspace.workspace_id
    assert payload["capability_key"] == "standard_chat"
    assert payload["requested_resource"] == "gemini"
    assert payload["resolved_resource"] == "gemini"
    assert payload["connector_id"] == "google_adapter"
    assert payload["activation_source"] == "org_switchboard"
    assert payload["enforced"] is True
    assert payload["allowed"] is True


def test_attach_execution_plan_to_request_diagnostics():
    begin_request_diagnostics(route="/test", ctx=_org_ctx(), text_hint="hello")
    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="gpt")

    attach_execution_plan_to_request_diagnostics(plan)

    diag = get_request_diagnostics()
    assert diag is not None
    assert diag.execution_capability_key == "standard_chat"
    assert diag.execution_requested_resource == "gpt"
    assert diag.execution_resolved_resource == "gpt"
    assert diag.execution_connector_id == "openai_adapter"
    assert diag.execution_activation_source == "org_switchboard"
    assert diag.execution_enforced is True
    assert diag.execution_allowed is True
    assert diag.execution_requested_capability == "engine-grok"
    assert diag.execution_workspace_context_id == workspace.context_id
    assert diag.execution_org_policy_allowed is True
    assert diag.execution_workspace_intent_enabled is None
    assert diag.execution_enforcement_owner == "execution_plan"
    assert diag.execution_denial_reason is None


def test_execution_plan_resolver_class():
    resolver = ExecutionPlanResolver()
    plan = resolver.resolve_plan(_standalone_workspace(), "council")
    assert plan.capability_key == "council"
    assert plan.enforced is False


def test_capability_ownership_contract_fields():
    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="claude")

    assert plan.enforcement_owner == "execution_plan"
    assert plan.requested_capability == "engine-claude"
    assert plan.workspace_context_id == workspace.context_id
    assert plan.org_policy_allowed is True
    assert plan.workspace_intent_enabled is None
    assert plan.allowed is True
    assert plan.enforced is True


def test_capability_ownership_contract_denied_unchanged(tmp_path, monkeypatch):
    system_db = tmp_path / "inactive_system_main.db"
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    init_global_service_schema()

    plan = resolve_execution_plan(
        _standalone_workspace(),
        "standard_chat",
        requested_resource="gemini",
    )
    assert plan.enforcement_owner == "execution_plan"
    assert plan.requested_capability == "engine-gemini"
    assert plan.org_policy_allowed is False
    assert plan.allowed is False
    assert plan.workspace_intent_enabled is None


def test_capability_ownership_contract_council_non_enforcing():
    plan = resolve_execution_plan(_standalone_workspace(), "council")
    assert plan.enforcement_owner == "execution_plan"
    assert plan.requested_capability == "council"
    assert plan.org_policy_allowed is None
    assert plan.workspace_intent_enabled is None
    assert plan.allowed is True
    assert plan.enforced is False


def test_resolve_workspace_intent_enabled_noop_returns_none():
    assert resolve_workspace_intent_enabled("ctx-123", "engine-claude") is None
    assert resolve_workspace_intent_enabled(None, None) is None
    assert resolve_workspace_intent_enabled("ctx-123", "council") is None


def test_workspace_intent_populated_only_via_helper(monkeypatch):
    from services.execution_plan import resolve_workspace_intent_enabled as real_resolver

    calls: list[tuple[str | None, str | None]] = []

    def _tracking_resolver(workspace_context_id, requested_capability):
        calls.append((workspace_context_id, requested_capability))
        return real_resolver(workspace_context_id, requested_capability)

    monkeypatch.setattr(
        "services.execution_plan.resolve_workspace_intent_enabled",
        _tracking_resolver,
    )

    workspace = _standalone_workspace()
    plan = resolve_execution_plan(workspace, "standard_chat", requested_resource="claude")

    assert len(calls) == 1
    assert calls[0][0] == workspace.context_id
    assert calls[0][1] == "engine-claude"
    assert plan.workspace_intent_enabled is None
    assert plan.allowed is True


def test_workspace_intent_does_not_add_switchboard_reads(monkeypatch):
    reads = {"count": 0}

    def _counting_active(org_id, provider_id):
        reads["count"] += 1
        return True

    monkeypatch.setattr(
        "services.execution_plan.is_provider_engine_active",
        _counting_active,
    )

    plan = resolve_execution_plan(
        _standalone_workspace(),
        "standard_chat",
        requested_resource="gpt",
    )

    assert reads["count"] == 1
    assert plan.workspace_intent_enabled is None
    assert plan.allowed is True
