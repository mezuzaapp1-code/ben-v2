"""Per-thread SQLite store — schema, ordering, anchor truncation."""
from __future__ import annotations

import uuid

import pytest

from database import thread_store


@pytest.fixture()
def thread_id(tmp_path, monkeypatch):
    monkeypatch.setattr(thread_store, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(thread_store, "_SYSTEM_DB", tmp_path / "system_main.db")
    monkeypatch.setattr(thread_store, "_THREADS_DIR", tmp_path / "threads")
    from services import project_tools

    monkeypatch.setattr(project_tools, "_PROJECTS_ROOT", tmp_path / "projects")
    return str(uuid.uuid4())


def test_insert_and_order_with_expert_anchor(thread_id):
    first = thread_store.insert_thread_message(thread_id, role="user", content="Hello")
    second = thread_store.insert_thread_message(thread_id, role="assistant", content="Hi there")
    expert = thread_store.insert_thread_message(
        thread_id,
        role="assistant",
        content="Guest view",
        provider="claude",
        message_type="expert_consult",
        insert_after_id=first,
    )
    ordered = thread_store.list_thread_messages(thread_id)
    assert [m.id for m in ordered] == [first, expert, second]


def test_list_until_anchor(thread_id):
    a = thread_store.insert_thread_message(thread_id, role="user", content="A")
    thread_store.insert_thread_message(thread_id, role="assistant", content="B")
    thread_store.insert_thread_message(thread_id, role="user", content="C")
    until = thread_store.list_thread_messages_until(thread_id, a)
    assert len(until) == 1
    assert until[0].content == "A"


def test_wal_and_foreign_keys_enabled(thread_id):
    with thread_store.get_thread_db_connection(thread_id) as conn:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert str(journal).lower() == "wal"
    assert int(fk) == 1


def test_delete_thread_metadata_and_database_file(thread_id):
    org_id = "11111111-1111-1111-1111-111111111111"
    thread_store.upsert_thread_metadata(
        thread_id=thread_id,
        org_id=org_id,
        title="Demo",
    )
    thread_store.insert_thread_message(thread_id, role="user", content="Hi")
    assert thread_store.get_thread_metadata(thread_id) is not None
    assert thread_store.legacy_thread_db_path(thread_id).exists()

    assert thread_store.delete_thread_metadata(thread_id, org_id) is True
    assert thread_store.get_thread_metadata(thread_id) is None
    assert thread_store.delete_thread_database_file(thread_id) is True
    assert not thread_store.legacy_thread_db_path(thread_id).exists()


def test_portable_db_path_for_project_setup(thread_id):
    org_id = "11111111-1111-1111-1111-111111111111"
    thread_store.upsert_thread_metadata(
        thread_id=thread_id,
        org_id=org_id,
        title="Portable",
        session_type="project_setup",
        project_slug="alpha-site",
    )
    path = thread_store.resolve_thread_db_path(thread_id)
    assert path.name == "project_context.db"
    assert "alpha-site" in str(path)
    thread_store.insert_thread_message(thread_id, role="user", content="Portable hello")
    assert path.exists()


def test_promote_thread_to_portable_storage_moves_sqlite_file(thread_id):
    org_id = "11111111-1111-1111-1111-111111111111"
    thread_store.upsert_thread_metadata(thread_id=thread_id, org_id=org_id, title="Chat")
    thread_store.insert_thread_message(thread_id, role="user", content="Before promote")
    legacy = thread_store.legacy_thread_db_path(thread_id)
    assert legacy.exists()

    payload = thread_store.promote_thread_to_portable_storage(
        thread_id=thread_id,
        org_id=org_id,
        project_slug="basalt-hq",
    )
    assert payload["session_type"] == "project_setup"
    assert payload["project_slug"] == "basalt-hq"
    assert not legacy.exists()
    portable = thread_store.project_context_db_path("basalt-hq")
    assert portable.exists()
    messages = thread_store.list_thread_messages(thread_id)
    assert messages[0].content == "Before promote"
