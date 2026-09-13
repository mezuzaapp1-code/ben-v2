"""Knowledge base SQLite store remains; prompt injection is contained."""
from __future__ import annotations

import pytest

from database.knowledge_store import init_knowledge_store
from services.knowledge_injection import inject_knowledge_few_shot, wrap_with_few_shot
from services.knowledge_service import (
    add_knowledge_document,
    build_knowledge_few_shot_block,
    create_knowledge_base,
    list_knowledge_bases,
)


@pytest.fixture
def kb_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_knowledge.db"
    monkeypatch.setenv("BEN_KNOWLEDGE_DB_PATH", str(db_path))
    init_knowledge_store()
    return db_path


@pytest.mark.asyncio
async def test_create_base_persists_without_prompt_injection(kb_db):
    base = await create_knowledge_base("RMS")
    await add_knowledge_document(
        base["id"],
        title="Gold RMS Template",
        content="Step 1: intake\nStep 2: classify",
    )
    block = await build_knowledge_few_shot_block("build an RMS based on the RMS base")
    assert block == ""
    names = [row["name"] for row in await list_knowledge_bases()]
    assert "RMS" in names
    assert kb_db.exists()


@pytest.mark.asyncio
async def test_inject_knowledge_does_not_wrap_payload(kb_db):
    await create_knowledge_base("Templates")
    await add_knowledge_document(
        (await list_knowledge_bases())[0]["id"],
        title="Checklist",
        content="Item A",
    )
    inner = "<user_message>\nhello\n</user_message>"
    out = await inject_knowledge_few_shot("use Templates knowledge", inner)
    assert "<few_shot_examples>" not in out
    assert "Item A" not in out
    assert "<user_message>" in out


def test_wrap_with_few_shot_passthrough():
    assert wrap_with_few_shot(few_shot_block="", inner_payload="plain") == "plain"
