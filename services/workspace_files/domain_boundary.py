"""
News ↔ Workspace File Library domain boundary (non-negotiable).

News owns:
  - NewsSource, NewsArticle, ManagedTopic, NewsEvent, EventPackage
  - SourceDocumentVersion (acquired news content only)
  - Managed Topic Analysis
  - News routers, services, indexes, and authorization

Workspace Files owns:
  - user uploads (WorkspaceFile)
  - file storage references under projects_root()/_workspace_files/
  - workspace ownership (org_id + workspace_id)
  - processing status, preview/download, workspace-bounded search
  - optional chat_id / project_id references (never News FKs)

Hard rules:
  1. A user-uploaded file must never become a NewsArticle or SourceDocumentVersion.
  2. A NewsArticle or acquired news document must never appear in Workspace Files.
  3. Do not reuse News models, routers, service paths, indexes, or auth for File Library.
  4. Shared low-level utilities only if domain-neutral (checksum, MIME, parsers, storage client).
  5. Shared business persistence is forbidden.
  6. Search indexes / retrieval namespaces remain separate.
  7. No News ↔ Files references, automatic transfer, or cross-domain retrieval.
  8. Manual user copy/upload is the only transfer mechanism.

Rejected path: reusing SourceDocumentVersion (or any News table) for Workspace uploads.
Canonical path: ben.workspace_files + services.workspace_files.* only.
"""

from __future__ import annotations

# Modules this package must never import (business / persistence coupling).
FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "services.news",
    "routers.news",
    "routers.news_product",
    "routers.news_sources",
    "routers.news_managed_topics",
)

# SQLAlchemy / table names that must never appear in File Library persistence.
FORBIDDEN_MODEL_NAMES: frozenset[str] = frozenset(
    {
        "NewsSource",
        "NewsArticle",
        "ManagedTopic",
        "NewsEvent",
        "EventPackage",
        "SourceDocumentVersion",
        "ManagedTopicAnalysis",
        "ManagedTopicAnalysisRun",
        "DocumentClassification",
        "EvidenceExtractionRun",
        "DocumentSpan",
    }
)

FORBIDDEN_TABLE_NAMES: frozenset[str] = frozenset(
    {
        "news_sources",
        "news_articles",
        "managed_topics",
        "news_events",
        "event_packages",
        "source_document_versions",
        "managed_topic_analyses",
        "managed_topic_analysis_runs",
        "document_classifications",
        "evidence_extraction_runs",
        "document_spans",
    }
)

# Canonical File Library ownership surface.
OWNED_MODEL_NAME = "WorkspaceFile"
OWNED_TABLE_NAME = "workspace_files"
OWNED_SERVICE_PACKAGE = "services.workspace_files"
OWNED_ROUTER_MODULE = "routers.workspace_files"
OWNED_STORAGE_SUBDIR = "_workspace_files"
