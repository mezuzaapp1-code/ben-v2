"""Execution plan resolution — Phase 3 observability, Phase 4 chat enforcement."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.engine_capability_gate import (
    catalog_key_for_provider,
    is_provider_engine_active,
)
from services.ops.structured_log import log_info
from services.workspace_resolver import WorkspaceContext

ActivationSource = str

_CHAT_CAPABILITY_KEYS = frozenset({"standard_chat", "standard-chat"})


def _normalize_capability_key(raw: str) -> str:
    return str(raw or "").strip().lower().replace("-", "_")


def _is_chat_capability(capability_key: str) -> bool:
    token = str(capability_key or "").strip().lower()
    return token in _CHAT_CAPABILITY_KEYS or _normalize_capability_key(token) == "standard_chat"


def _normalize_resource_token(raw: str | None) -> str | None:
    token = str(raw or "").strip()
    return token or None


def _provider_id_from_resource(resource: str | None) -> str | None:
    token = _normalize_resource_token(resource)
    if not token:
        return None
    lower = token.lower()
    if lower.startswith("claude"):
        return "claude"
    if lower.startswith("gemini"):
        return "gemini"
    if lower.startswith("gpt"):
        return "gpt"
    return None


def _resolve_connector_id(resource: str | None) -> str | None:
    """Map resource prefix to connector label (diagnostic only — no routing)."""
    provider_id = _provider_id_from_resource(resource)
    if provider_id == "claude":
        return "anthropic_adapter"
    if provider_id == "gemini":
        return "google_adapter"
    if provider_id == "gpt":
        return "openai_adapter"
    return None


@dataclass(frozen=True)
class ExecutionPlan:
    """Runtime boarding pass — sole canonical permission object; chat paths may enforce."""

    org_id: str
    workspace_id: str | None
    workspace_type: str
    capability_key: str
    requested_resource: str | None
    resolved_resource: str | None
    connector_id: str | None
    activation_source: ActivationSource = "diagnostic_only"
    enforced: bool = False
    allowed: bool = True
    requested_capability: str | None = None
    workspace_context_id: str | None = None
    org_policy_allowed: bool | None = None
    workspace_intent_enabled: bool | None = None
    enforcement_owner: str = "execution_plan"
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _workspace_context_id(workspace_context: WorkspaceContext) -> str | None:
    token = getattr(workspace_context, "context_id", None) or workspace_context.workspace_id
    return str(token).strip() if token else None


def _requested_capability_label(
    capability_key: str,
    *,
    requested_resource: str | None,
) -> str | None:
    provider_id = _provider_id_from_resource(requested_resource)
    catalog_key = catalog_key_for_provider(provider_id) if provider_id else None
    if catalog_key:
        return catalog_key
    cap = str(capability_key or "").strip()
    return cap or None


def resolve_workspace_intent_enabled(
    workspace_context_id: str | None,
    requested_capability: str | None,
) -> bool | None:
    """
    Workspace-scoped capability intent (frontend checkbox target).

    No-op placeholder — returns None until workspace intent storage exists.
    Does not query DB, infer True, or affect allow/deny decisions.
    """
    _ = workspace_context_id
    _ = requested_capability
    return None


def _ownership_contract_kwargs(
    workspace_context: WorkspaceContext,
    *,
    capability_key: str,
    requested_resource: str | None,
    org_policy_allowed: bool | None,
) -> dict[str, Any]:
    """Capability ownership contract — metadata only; does not alter allow/deny."""
    context_id = _workspace_context_id(workspace_context)
    requested_capability = _requested_capability_label(
        capability_key,
        requested_resource=requested_resource,
    )
    return {
        "requested_capability": requested_capability,
        "workspace_context_id": context_id,
        "org_policy_allowed": org_policy_allowed,
        "workspace_intent_enabled": resolve_workspace_intent_enabled(
            context_id,
            requested_capability,
        ),
        "enforcement_owner": "execution_plan",
    }


class ExecutionPlanResolver:
    """Resolves execution plans from workspace context and capability intent."""

    def resolve_plan(
        self,
        workspace_context: WorkspaceContext,
        capability_key: str,
        *,
        requested_resource: str | None = None,
    ) -> ExecutionPlan:
        cap = str(capability_key or "").strip()
        if not cap:
            raise ValueError("capability_key is required")

        requested = _normalize_resource_token(requested_resource)
        resolved = requested
        connector = _resolve_connector_id(requested)

        if not _is_chat_capability(cap):
            return ExecutionPlan(
                org_id=workspace_context.org_id,
                workspace_id=workspace_context.workspace_id,
                workspace_type=workspace_context.workspace_type,
                capability_key=cap,
                requested_resource=requested,
                resolved_resource=resolved,
                connector_id=connector,
                activation_source="diagnostic_only",
                enforced=False,
                allowed=True,
                diagnostics={"phase": "observability_only"},
                **_ownership_contract_kwargs(
                    workspace_context,
                    capability_key=cap,
                    requested_resource=requested,
                    org_policy_allowed=None,
                ),
            )

        provider_id = _provider_id_from_resource(requested)
        if provider_id is None:
            return ExecutionPlan(
                org_id=workspace_context.org_id,
                workspace_id=workspace_context.workspace_id,
                workspace_type=workspace_context.workspace_type,
                capability_key=cap,
                requested_resource=requested,
                resolved_resource=resolved,
                connector_id=connector,
                activation_source="diagnostic_only",
                enforced=False,
                allowed=True,
                diagnostics={"phase": "chat_enforcement_skipped", "reason": "no_gatable_provider"},
                **_ownership_contract_kwargs(
                    workspace_context,
                    capability_key=cap,
                    requested_resource=requested,
                    org_policy_allowed=None,
                ),
            )

        catalog_key = catalog_key_for_provider(provider_id)
        active = is_provider_engine_active(workspace_context.org_id, provider_id)
        return ExecutionPlan(
            org_id=workspace_context.org_id,
            workspace_id=workspace_context.workspace_id,
            workspace_type=workspace_context.workspace_type,
            capability_key=cap,
            requested_resource=requested,
            resolved_resource=resolved,
            connector_id=connector,
            activation_source="org_switchboard",
            enforced=True,
            allowed=active,
            diagnostics={
                "phase": "chat_enforcement",
                "provider_id": provider_id,
                "catalog_key": catalog_key,
            },
            **_ownership_contract_kwargs(
                workspace_context,
                capability_key=cap,
                requested_resource=requested,
                org_policy_allowed=active,
            ),
        )


_default_resolver = ExecutionPlanResolver()


def resolve_execution_plan(
    workspace_context: WorkspaceContext,
    capability_key: str,
    *,
    requested_resource: str | None = None,
) -> ExecutionPlan:
    """Resolve an execution plan for the given workspace and capability."""
    return _default_resolver.resolve_plan(
        workspace_context,
        capability_key,
        requested_resource=requested_resource,
    )


def execution_plan_to_log_payload(plan: ExecutionPlan) -> dict[str, Any]:
    """Safe structured-log fields for request diagnostics (no secrets)."""
    return {
        "workspace_id": plan.workspace_id,
        "capability_key": plan.capability_key,
        "requested_resource": plan.requested_resource,
        "resolved_resource": plan.resolved_resource,
        "connector_id": plan.connector_id,
        "activation_source": plan.activation_source,
        "workspace_type": plan.workspace_type,
        "enforced": plan.enforced,
        "allowed": plan.allowed,
        "requested_capability": plan.requested_capability,
        "workspace_context_id": plan.workspace_context_id,
        "org_policy_allowed": plan.org_policy_allowed,
        "workspace_intent_enabled": plan.workspace_intent_enabled,
        "enforcement_owner": plan.enforcement_owner,
    }


def log_execution_plan_resolved(plan: ExecutionPlan) -> None:
    log_info(
        "execution plan resolved",
        subsystem="execution_plan",
        operation="resolve_execution_plan",
        outcome="ok" if plan.allowed else "denied",
        **execution_plan_to_log_payload(plan),
    )
