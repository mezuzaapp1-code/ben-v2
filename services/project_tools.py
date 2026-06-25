"""Local filesystem project workspace tools for autonomous project onboarding."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_PROJECTS_ROOT = Path(__file__).resolve().parents[1] / "data" / "projects"
_SLUG_RE = re.compile(r"[^a-z0-9\-_]+")


def projects_root() -> Path:
    raw = (os.getenv("BEN_PROJECTS_DATA_DIR") or "").strip()
    return Path(raw) if raw else _PROJECTS_ROOT


def slugify_project_name(project_name_slug: str) -> str:
    slug = _SLUG_RE.sub("-", str(project_name_slug or "").strip().lower())
    slug = slug.strip("-")[:64]
    return slug or "project"


def _project_root(project_name_slug: str) -> Path:
    root = projects_root() / slugify_project_name(project_name_slug)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _resolve_write_target(project_name_slug: str, filename: str) -> Path:
    root = _project_root(project_name_slug)
    name = str(filename or "").strip().replace("\\", "/")
    if not name:
        raise ValueError("filename is required")
    if ".." in name or name.startswith("/"):
        raise ValueError("invalid filename path")
    parts = [p for p in name.split("/") if p]
    if not parts:
        raise ValueError("invalid filename path")
    if parts[0] not in {"specs", "tasks"} and len(parts) == 1:
        lowered = parts[0].lower()
        if lowered in {"spec.md", "specs.md", "specification.md"}:
            parts = ["specs", parts[0]]
        elif lowered in {"tasks.md", "task.md", "todo.md"}:
            parts = ["tasks", parts[0]]
    target = (root / Path(*parts)).resolve()
    if root not in target.parents and target != root:
        raise ValueError("filename escapes project workspace")
    return target


def create_project_directory(project_name_slug: str) -> str:
    """Provision data/projects/{slug}/ with /specs and /tasks sub-folders."""
    slug = slugify_project_name(project_name_slug)
    root = _project_root(slug)
    (root / "specs").mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    return json.dumps(
        {
            "status": "ok",
            "project_slug": slug,
            "path": str(root),
            "message": f"Workspace ready at data/projects/{slug}/ with specs/ and tasks/ folders.",
        },
        ensure_ascii=False,
    )


def write_project_file(project_name_slug: str, filename: str, content: str) -> str:
    """Write UTF-8 markdown, spec, or Python files into the project workspace."""
    target = _resolve_write_target(project_name_slug, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(content or ""), encoding="utf-8")
    rel = target.relative_to(projects_root())
    return json.dumps(
        {
            "status": "ok",
            "path": str(rel).replace("\\", "/"),
            "bytes": len(str(content or "").encode("utf-8")),
            "message": f"Wrote {rel} ({len(str(content or ''))} characters).",
        },
        ensure_ascii=False,
    )


def delete_project_directory(project_name_slug: str) -> str:
    """Remove data/projects/{slug}/ and all contents when present."""
    slug = slugify_project_name(project_name_slug)
    root = projects_root() / slug
    if not root.exists():
        return json.dumps(
            {"status": "ok", "project_slug": slug, "message": f"No workspace folder found for {slug}."},
            ensure_ascii=False,
        )
    resolved = root.resolve()
    if projects_root().resolve() not in resolved.parents:
        raise ValueError("invalid project path")
    import shutil

    shutil.rmtree(resolved)
    return json.dumps(
        {
            "status": "ok",
            "project_slug": slug,
            "message": f"Removed workspace folder data/projects/{slug}/.",
        },
        ensure_ascii=False,
    )


def initialize_project_files(
    project_slug: str,
    architecture_markdown: str,
    roadmap_markdown: str,
) -> str:
    """Provision data/projects/{slug}/ and write specs/architecture.md + tasks/roadmap.md."""
    slug = slugify_project_name(project_slug)
    root = _project_root(slug)
    (root / "specs").mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(parents=True, exist_ok=True)

    arch_path = _resolve_write_target(slug, "specs/architecture.md")
    roadmap_path = _resolve_write_target(slug, "tasks/roadmap.md")
    arch_path.write_text(str(architecture_markdown or ""), encoding="utf-8")
    roadmap_path.write_text(str(roadmap_markdown or ""), encoding="utf-8")

    return json.dumps(
        {
            "status": "ok",
            "project_slug": slug,
            "path": str(root),
            "files": [
                "specs/architecture.md",
                "tasks/roadmap.md",
            ],
            "bytes": {
                "architecture": len(str(architecture_markdown or "").encode("utf-8")),
                "roadmap": len(str(roadmap_markdown or "").encode("utf-8")),
            },
            "message": (
                f"Initialized workspace data/projects/{slug}/ with "
                "specs/architecture.md and tasks/roadmap.md."
            ),
        },
        ensure_ascii=False,
    )


_WORKSPACE_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "initialize_project_files",
        "description": (
            "Create the on-disk project workspace under data/projects/{slug}/ and write "
            "specs/architecture.md and tasks/roadmap.md in one atomic provisioning step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_slug": {
                    "type": "string",
                    "description": "URL-safe project slug (e.g. ben-hq-refactor).",
                },
                "architecture_markdown": {
                    "type": "string",
                    "description": "UTF-8 markdown body for specs/architecture.md.",
                },
                "roadmap_markdown": {
                    "type": "string",
                    "description": "UTF-8 markdown body for tasks/roadmap.md.",
                },
            },
            "required": ["project_slug", "architecture_markdown", "roadmap_markdown"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_project_directory",
        "description": (
            "Create the on-disk workspace for a new project under data/projects/{slug}/ "
            "including specs/ and tasks/ subdirectories."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_name_slug": {
                    "type": "string",
                    "description": "URL-safe project slug (e.g. ben-hq-refactor).",
                }
            },
            "required": ["project_name_slug"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_project_file",
        "description": (
            "Write spec.md, tasks.md, Python modules, or other UTF-8 files into the project workspace. "
            "Use paths like specs/spec.md, tasks/tasks.md, or main.py."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_name_slug": {"type": "string"},
                "filename": {
                    "type": "string",
                    "description": "Relative path inside the project folder (e.g. specs/spec.md).",
                },
                "content": {"type": "string", "description": "UTF-8 file body."},
            },
            "required": ["project_name_slug", "filename", "content"],
            "additionalProperties": False,
        },
    },
]


def openai_project_tools() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": dict(spec)}
        for spec in _WORKSPACE_TOOL_SPECS
    ]


def anthropic_project_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "input_schema": spec["parameters"],
        }
        for spec in _WORKSPACE_TOOL_SPECS
    ]


def gemini_project_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "parameters": spec["parameters"],
        }
        for spec in _WORKSPACE_TOOL_SPECS
    ]


def project_tools_for_provider(provider_id: str | None) -> list[dict[str, Any]]:
    provider = (provider_id or "openai").strip().lower()
    if provider in {"claude", "anthropic"}:
        return anthropic_project_tools()
    if provider in {"gemini", "google"}:
        return gemini_project_tools()
    return openai_project_tools()


PROJECT_AGENT_TOOL_DEFINITIONS: list[dict[str, Any]] = openai_project_tools()

PROJECT_AGENT_TOOL_NAMES: frozenset[str] = frozenset(
    spec["name"] for spec in _WORKSPACE_TOOL_SPECS
)

TOOL_TELEMETRY_LABELS: dict[str, str] = {
    "initialize_project_files": (
        "⚙️ System: Provisioning project workspace with architecture and roadmap manifests..."
    ),
    "create_project_directory": (
        "⚙️ System: Deploying physical project folder and configuration profiles to disk..."
    ),
    "write_project_file": (
        "⚙️ System: Writing project specification and task manifests to workspace..."
    ),
}


def execute_project_agent_tool(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Execute a registered local filesystem tool and return a JSON string payload."""
    args = arguments or {}
    if tool_name == "initialize_project_files":
        return initialize_project_files(
            str(args.get("project_slug") or ""),
            str(args.get("architecture_markdown") or ""),
            str(args.get("roadmap_markdown") or ""),
        )
    if tool_name == "create_project_directory":
        return create_project_directory(str(args.get("project_name_slug") or ""))
    if tool_name == "write_project_file":
        return write_project_file(
            str(args.get("project_name_slug") or ""),
            str(args.get("filename") or ""),
            str(args.get("content") or ""),
        )
    return json.dumps({"status": "error", "message": f"Unknown tool: {tool_name}"})
