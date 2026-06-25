"""Tests for workspace thread filtering helpers."""
from __future__ import annotations

import pytest

from database.thread_store import get_thread_project_slug, upsert_thread_metadata
from services.thread_service import delete_thread


@pytest.fixture
def thread_id():
    return "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def test_get_thread_project_slug_reads_metadata_only(thread_id):
    org_id = "00000000-0000-0000-0000-000000000001"
    upsert_thread_metadata(
        thread_id=thread_id,
        org_id=org_id,
        title="Portable workspace",
        session_type="project_setup",
        project_slug="alpha-site",
    )
    assert get_thread_project_slug(thread_id) == "alpha-site"


@pytest.mark.asyncio
async def test_delete_thread_skips_message_scan_for_slug(thread_id, monkeypatch):
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    tid = uuid.UUID(thread_id)

    upsert_thread_metadata(
        thread_id=thread_id,
        org_id=str(org_id),
        title="Portable workspace",
        session_type="project_setup",
        project_slug="alpha-site",
    )

    mock_row = MagicMock()
    mock_row.title = "Portable workspace"
    mock_pg = MagicMock()
    mock_pg.org_id = org_id
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_pg)
    mock_session.delete = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.execute = AsyncMock()

    def forbid_message_scan(*_args, **_kwargs):
        raise AssertionError("delete_thread must not scan thread messages for slug resolution")

    with patch("services.thread_service.get_thread_for_org", new=AsyncMock(return_value=mock_row)), patch(
        "services.thread_service.get_db_session"
    ) as mock_sess, patch("services.thread_service.release_thread_database_files"), patch(
        "services.thread_service.delete_thread_metadata", return_value=True
    ), patch("services.project_tools.delete_project_directory"), patch(
        "services.thread_service.list_thread_messages", side_effect=forbid_message_scan
    ):
        mock_sess.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await delete_thread(org_id, tid)

    assert result["project_slug"] == "alpha-site"
    assert result["deleted"] is True
