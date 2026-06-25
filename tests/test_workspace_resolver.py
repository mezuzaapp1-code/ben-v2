import pytest
from services.workspace_resolver import (
    WorkspaceResolver,
    WorkspaceContext,
    derive_workspace_id_from_slug,
    slugify_project_name,
)

def test_resolve_context_with_slug():
    org_id = "org_123"
    project_slug = "ben-development"
    context = WorkspaceResolver.resolve_context(org_id=org_id, project_slug=project_slug)
    assert isinstance(context, WorkspaceContext)
    assert context.org_id == org_id
    assert context.project_slug == project_slug
    assert context.context_id == context.workspace_id
    assert context.workspace_type == "project"

def test_resolve_context_fallback():
    org_id = "org_123"
    context = WorkspaceResolver.resolve_context(org_id=org_id)
    assert isinstance(context, WorkspaceContext)
    assert context.org_id == org_id
    assert context.context_id == context.workspace_id
    assert context.workspace_type == "standalone"


def test_slug_normalization_prevents_fragmentation():
    org_id = "org_123"
    a = derive_workspace_id_from_slug(org_id, "Alpha Site")
    b = derive_workspace_id_from_slug(org_id, "alpha-site")
    assert a == b
    assert slugify_project_name("Alpha Site") == "alpha-site"
