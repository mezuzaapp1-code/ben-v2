"""Local project filesystem tools."""
from __future__ import annotations

import json

import pytest

from services import project_tools


@pytest.fixture()
def projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(project_tools, "_PROJECTS_ROOT", tmp_path)
    return tmp_path


def test_create_project_directory_provisions_subfolders(projects_dir):
    payload = json.loads(project_tools.create_project_directory("BEN HQ Refactor"))
    assert payload["status"] == "ok"
    slug = payload["project_slug"]
    root = projects_dir / slug
    assert (root / "specs").is_dir()
    assert (root / "tasks").is_dir()


def test_write_project_file_rejects_traversal(projects_dir):
    project_tools.create_project_directory("safe-project")
    with pytest.raises(ValueError):
        project_tools.write_project_file("safe-project", "../escape.md", "nope")


def test_write_project_file_writes_utf8_markdown(projects_dir):
    project_tools.create_project_directory("demo")
    payload = json.loads(
        project_tools.write_project_file("demo", "specs/spec.md", "# Spec\nHello")
    )
    assert payload["status"] == "ok"
    assert (projects_dir / "demo" / "specs" / "spec.md").read_text(encoding="utf-8") == "# Spec\nHello"


def test_delete_project_directory_removes_workspace(projects_dir):
    payload = json.loads(project_tools.create_project_directory("remove-me"))
    slug = payload["project_slug"]
    assert (projects_dir / slug).is_dir()
    delete_payload = json.loads(project_tools.delete_project_directory(slug))
    assert delete_payload["status"] == "ok"
    assert not (projects_dir / slug).exists()


def test_initialize_project_files_writes_architecture_and_roadmap(projects_dir):
    payload = json.loads(
        project_tools.initialize_project_files(
            "ben-hq",
            "# Architecture\nCore stack",
            "# Roadmap\nPhase 1",
        )
    )
    assert payload["status"] == "ok"
    root = projects_dir / "ben-hq"
    assert (root / "specs" / "architecture.md").read_text(encoding="utf-8") == "# Architecture\nCore stack"
    assert (root / "tasks" / "roadmap.md").read_text(encoding="utf-8") == "# Roadmap\nPhase 1"


def test_project_tools_for_provider_formats():
    openai = project_tools.openai_project_tools()
    anthropic = project_tools.anthropic_project_tools()
    gemini = project_tools.gemini_project_tools()
    assert openai[0]["type"] == "function"
    assert openai[0]["function"]["name"] == "initialize_project_files"
    assert anthropic[0]["name"] == "initialize_project_files"
    assert "input_schema" in anthropic[0]
    assert gemini[0]["name"] == "initialize_project_files"
    assert "parameters" in gemini[0]


def test_execute_project_agent_tool_unknown():
    payload = json.loads(project_tools.execute_project_agent_tool("missing", {}))
    assert payload["status"] == "error"
