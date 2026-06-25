import logging
import uuid
import re
from dataclasses import dataclass, asdict
from typing import Optional, Any, Dict

logger = logging.getLogger("ben.workspace_resolver")

# Canonical tracking headers and constants
CLIENT_WORKSPACE_ID_HEADER = "X-Workspace-ID"
BEN_WORKSPACE_NAMESPACE = uuid.UUID("ca769a23-42e6-42db-9eb5-8e6bfdf4b611")
_SLUG_RE = re.compile(r"[^a-z0-9-]+")

@dataclass(frozen=True)
class WorkspaceContext:
    org_id: str
    workspace_id: str
    context_id: str
    workspace_type: str  # "project" | "standalone"
    thread_id: Optional[str] = None
    project_id: Optional[str] = None
    project_slug: Optional[str] = None
    membership_verified: bool = False
    resolution_source: str = "none"


def slugify_project_name(project_name_slug: str) -> str:
    """Standardizes project slugs to prevent identity fragmentation."""
    slug = _SLUG_RE.sub("-", str(project_name_slug or "").strip().lower())
    slug = slug.strip("-")[:64]
    return slug or "project"


class WorkspaceResolver:
    @staticmethod
    def derive_id(org_id: str, kind: str, token: str) -> str:
        """Deterministic UUIDv5 namespace engine."""
        unique_key = f"{org_id}:{kind}:{token}"
        return str(uuid.uuid5(BEN_WORKSPACE_NAMESPACE, unique_key))

    @classmethod
    def resolve_context(
        cls,
        org_id: str,
        project_slug: Optional[str] = None,
        project_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        raw_client_workspace_id: Optional[str] = None,
        db_session: Any = None,
    ) -> WorkspaceContext:
        _ = db_session

        if raw_client_workspace_id:
            logger.warning("Raw client ID %s unverified", raw_client_workspace_id)

        effective_slug = str(project_slug or "").strip() or None
        effective_project_id = str(project_id or "").strip() or None
        source = "none"
        membership_verified = False
        thread_org_ok = False
        meta_slug: Optional[str] = None
        meta_project_id: Optional[str] = None
        session_type = "chat"

        tid = str(thread_id or "").strip() or None
        if tid:
            from database.thread_store import get_thread_metadata

            meta = get_thread_metadata(tid)
            if meta is None:
                logger.info("workspace thread metadata missing for thread_id=%s", tid)
            else:
                thread_org_ok = str(meta.get("org_id") or "").strip() == org_id
                if not thread_org_ok:
                    logger.warning(
                        "workspace thread org mismatch — ignoring thread-bound project fields "
                        "(org_id=%s thread_org_id=%s)",
                        org_id,
                        meta.get("org_id"),
                    )
                else:
                    meta_slug = str(meta.get("project_slug") or "").strip() or None
                    meta_project_id = str(meta.get("project_id") or "").strip() or None
                    session_type = str(meta.get("session_type") or "chat")

        if not effective_slug and thread_org_ok and meta_slug:
            effective_slug = meta_slug
            if not project_slug:
                source = "thread_metadata"
        if not effective_project_id and thread_org_ok and meta_project_id:
            effective_project_id = meta_project_id
            if not project_id and source == "none":
                source = "thread_metadata"

        if effective_slug:
            normalized_slug = slugify_project_name(effective_slug)
            derived_id = cls.derive_id(org_id, "project", normalized_slug)
            if source == "none":
                source = "slug"
            if project_slug:
                source = "thread_metadata+project_slug" if source == "thread_metadata" else "project_slug"
            membership_verified = bool(
                thread_org_ok
                and (meta_slug or meta_project_id or session_type == "project_setup")
            )
            if effective_slug and project_slug and not tid:
                membership_verified = False

            logger.info(
                "[CONTEXT NAMESPACE] Execution isolated. Retrieval scoped strictly to Context ID: %s",
                derived_id,
            )
            return WorkspaceContext(
                org_id=org_id,
                workspace_id=derived_id,
                context_id=derived_id,
                workspace_type="project",
                thread_id=tid,
                project_id=effective_project_id,
                project_slug=normalized_slug,
                membership_verified=membership_verified,
                resolution_source=source,
            )

        if effective_project_id:
            derived_id = cls.derive_id(org_id, "project_id", effective_project_id)
            if source == "none":
                source = "project_id"
            membership_verified = bool(
                thread_org_ok
                and (meta_slug or meta_project_id or session_type == "project_setup")
            )
            if effective_project_id and project_id and not tid:
                membership_verified = False

            logger.info(
                "[CONTEXT NAMESPACE] Execution isolated. Retrieval scoped strictly to Context ID: %s",
                derived_id,
            )
            return WorkspaceContext(
                org_id=org_id,
                workspace_id=derived_id,
                context_id=derived_id,
                workspace_type="project",
                thread_id=tid,
                project_id=effective_project_id,
                project_slug=None,
                membership_verified=membership_verified,
                resolution_source=source,
            )

        fallback_id = cls.derive_id(org_id, "standalone", "default")
        logger.info(
            "[CONTEXT NAMESPACE] Execution isolated. Retrieval scoped strictly to Context ID: %s",
            fallback_id,
        )
        return WorkspaceContext(
            org_id=org_id,
            workspace_id=fallback_id,
            context_id=fallback_id,
            workspace_type="standalone",
            thread_id=tid,
            project_id=None,
            project_slug=None,
            membership_verified=False,
            resolution_source="none",
        )


# =====================================================================
# P0: INTEGRATION FACADE & COMPATIBILITY LAYER
# =====================================================================

def derive_workspace_id_from_slug(org_id: str, project_slug: str) -> str:
    return WorkspaceResolver.derive_id(org_id, "project", slugify_project_name(project_slug))


def org_id_from_tenant(ctx: Any) -> str:
    """Effective org scope — from server auth, never client JSON."""
    scope = getattr(ctx, "scope_org_id", None)
    if scope is not None:
        return str(scope)
    org = getattr(ctx, "org_id", None)
    if org:
        return str(org)
    return str(getattr(ctx, "tenant_id", "default_org"))


def resolve_workspace_context(
    ctx: Any,
    thread_id: Optional[str] = None,
    project_id: Optional[str] = None,
    project_slug: Optional[str] = None,
    client_workspace_id: Optional[str] = None,
) -> WorkspaceContext:
    org_id = org_id_from_tenant(ctx)
    return WorkspaceResolver.resolve_context(
        org_id=org_id,
        project_slug=project_slug,
        project_id=project_id,
        thread_id=thread_id,
        raw_client_workspace_id=client_workspace_id,
    )


def resolve_workspace_context_for_org(
    org_id: str,
    project_slug: Optional[str] = None,
    project_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> WorkspaceContext:
    return WorkspaceResolver.resolve_context(
        org_id=org_id,
        project_slug=project_slug,
        project_id=project_id,
        thread_id=thread_id,
    )


def workspace_context_to_log_payload(context: WorkspaceContext) -> Dict[str, Any]:
    if not context:
        return {}
    return {
        "workspace_id": context.workspace_id,
        "context_id": context.context_id,
        "canonical_context_id": context.context_id,
        "workspace_type": context.workspace_type,
        "resolution_source": context.resolution_source,
        "membership_verified": context.membership_verified,
        "project_slug": context.project_slug,
        "project_id": context.project_id,
        "thread_id": context.thread_id,
    }
