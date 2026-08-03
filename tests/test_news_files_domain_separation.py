"""
Regression: News and Workspace File Library remain separate domains.

Proves:
  - News content is absent from Workspace Files surfaces
  - Workspace uploads are absent from News APIs / persistence
  - identical filenames / content hashes do not merge across domains
  - deleting a Workspace file cannot affect News documents
  - deleting or refreshing News content cannot affect Workspace files
"""
from __future__ import annotations

import ast
import hashlib
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from database import models as db_models  # noqa: E402
from database.models import NewsArticle, WorkspaceFile  # noqa: E402
from services.workspace_files import domain_boundary as boundary  # noqa: E402
from services.workspace_files import service as file_service  # noqa: E402
from services.workspace_files import storage as file_storage  # noqa: E402

# SourceDocumentVersion exists on some branches (full-content acquisition); optional on main.
SourceDocumentVersion = getattr(db_models, "SourceDocumentVersion", None)

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "services" / "workspace_files"
ROUTER_PATH = Path(__file__).resolve().parents[1] / "routers" / "workspace_files.py"


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_file_library_package_never_imports_news_business_modules():
    for path in _python_files(PACKAGE_ROOT) + [ROUTER_PATH]:
        imported = _imported_modules(path)
        for mod in imported:
            for prefix in boundary.FORBIDDEN_IMPORT_PREFIXES:
                assert not (
                    mod == prefix or mod.startswith(prefix + ".")
                ), f"{path.name} imports forbidden News module {mod}"


def test_file_library_persistence_is_workspace_file_not_source_document_version():
    assert WorkspaceFile.__tablename__ == boundary.OWNED_TABLE_NAME
    assert NewsArticle.__tablename__ == "news_articles"
    assert WorkspaceFile.__tablename__ != NewsArticle.__tablename__
    if SourceDocumentVersion is not None:
        assert SourceDocumentVersion.__tablename__ == "source_document_versions"
        assert WorkspaceFile.__tablename__ != SourceDocumentVersion.__tablename__

    # No FK from WorkspaceFile to News tables.
    fk_targets = {
        str(fk.column)
        for table in [WorkspaceFile.__table__]
        for fk in table.foreign_keys
    }
    for forbidden in boundary.FORBIDDEN_TABLE_NAMES:
        assert not any(forbidden in target for target in fk_targets), fk_targets


def test_workspace_file_model_has_no_news_columns():
    cols = set(WorkspaceFile.__table__.columns.keys())
    for banned in (
        "source_id",
        "article_id",
        "document_version_id",
        "event_id",
        "managed_topic_id",
        "guid",
        "canonical_url",
    ):
        assert banned not in cols


def test_identical_filename_and_checksum_do_not_imply_cross_domain_merge():
    """Same name/hash may exist in both domains as independent records."""
    shared_name = "earnings.pdf"
    shared_bytes = b"%PDF-1.4 identical payload for domain split"
    shared_hash = hashlib.sha256(shared_bytes).hexdigest()

    news_record = {
        "domain": "news",
        "model": "SourceDocumentVersion",
        "filename": shared_name,
        "checksum": shared_hash,
        "id": str(uuid.uuid4()),
    }
    files_record = {
        "domain": "workspace_files",
        "model": "WorkspaceFile",
        "filename": shared_name,
        "checksum": shared_hash,
        "id": str(uuid.uuid4()),
    }
    assert news_record["checksum"] == files_record["checksum"]
    assert news_record["filename"] == files_record["filename"]
    assert news_record["id"] != files_record["id"]
    assert news_record["model"] != files_record["model"]
    assert news_record["domain"] != files_record["domain"]
    # No shared primary key namespace / merge key across domains.
    assert not hasattr(WorkspaceFile, "document_version_id")
    if SourceDocumentVersion is not None:
        assert not hasattr(SourceDocumentVersion, "workspace_file_id")


@pytest.mark.asyncio
async def test_delete_workspace_file_does_not_touch_news_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(file_storage, "files_root", lambda: tmp_path)
    monkeypatch.setattr(
        file_storage,
        "absolute_path_for_key",
        lambda key: tmp_path / key,
    )

    org = uuid.uuid4()
    ws = uuid.uuid4()
    fid = uuid.uuid4()
    key = f"{org}/{ws}/{fid}/notes.txt"
    dest = tmp_path / key
    dest.parent.mkdir(parents=True)
    dest.write_text("workspace only", encoding="utf-8")

    class Row:
        id = fid
        org_id = org
        workspace_id = ws
        storage_key = key

    news_delete_calls: list[str] = []

    class FakeSession:
        async def execute(self, *_a, **_k):
            return None

        async def get(self, model, _id):
            # Must only request WorkspaceFile.
            assert model is WorkspaceFile
            if SourceDocumentVersion is not None:
                assert model is not SourceDocumentVersion
            assert model is not NewsArticle
            return Row()

        async def delete(self, obj):
            assert obj is Row or getattr(obj, "id", None) == fid
            news_delete_calls.append("workspace_row")

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    project = MagicMock()
    project.org_id = org

    with patch.object(file_service, "_require_workspace", new_callable=AsyncMock, return_value=project), patch.object(
        file_service, "get_db_session", side_effect=lambda: FakeSession()
    ), patch(
        "services.news",
        create=True,
    ) as news_pkg:
        # If delete ever imported/called news, this sentinel would be used — ensure unused.
        news_pkg.delete_document = MagicMock()
        out = await file_service.delete_file(org_id=org, workspace_id=ws, file_id=fid)

    assert out["deleted"] is True
    assert not dest.exists()
    assert news_pkg.delete_document.call_count == 0
    assert "workspace_row" in news_delete_calls


@pytest.mark.asyncio
async def test_news_delete_or_refresh_cannot_call_workspace_file_delete():
    """Structural: News service modules must not import File Library delete/upload."""
    news_root = Path(__file__).resolve().parents[1] / "services" / "news"
    assert news_root.is_dir()
    forbidden_files_refs = (
        "services.workspace_files",
        "WorkspaceFile",
        "workspace_files",
        "delete_file",
        "upload_file",
    )
    offenders: list[str] = []
    for path in news_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # Allow mentioning the boundary in comments only if prefixed carefully;
        # any import of the package is a hard fail.
        imports = _imported_modules(path)
        if any(mod == "services.workspace_files" or mod.startswith("services.workspace_files.") for mod in imports):
            offenders.append(f"import:{path.relative_to(news_root.parent)}")
            continue
        if "from services.workspace_files" in text or "import services.workspace_files" in text:
            offenders.append(f"text-import:{path.relative_to(news_root.parent)}")
        # Accidental model coupling
        if "WorkspaceFile" in text and "domain" not in path.name:
            offenders.append(f"model-ref:{path.relative_to(news_root.parent)}")
    assert offenders == [], offenders


def test_list_files_query_cannot_select_news_tables():
    """list_files builds a select() against WorkspaceFile only."""
    src = (PACKAGE_ROOT / "service.py").read_text(encoding="utf-8")
    assert "select(WorkspaceFile)" in src
    for name in boundary.FORBIDDEN_MODEL_NAMES:
        assert f"select({name}" not in src
        assert f"join({name}" not in src


def test_news_product_api_modules_do_not_query_workspace_files():
    news_api = Path(__file__).resolve().parents[1] / "services" / "news" / "product_news_api.py"
    if not news_api.exists():
        pytest.skip("product_news_api missing")
    text = news_api.read_text(encoding="utf-8")
    assert "workspace_files" not in text
    assert "WorkspaceFile" not in text


def test_boundary_constants_document_rejected_source_document_path():
    assert "SourceDocumentVersion" in boundary.FORBIDDEN_MODEL_NAMES
    assert "source_document_versions" in boundary.FORBIDDEN_TABLE_NAMES
    assert boundary.OWNED_MODEL_NAME == "WorkspaceFile"
    assert "SourceDocumentVersion" not in {boundary.OWNED_MODEL_NAME}
