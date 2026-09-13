"""Hard safety boundary: this package has no production-mutation authority."""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_ACTIONS = (
    "merge_code",
    "deploy",
    "modify_production_flags",
    "modify_production_schema",
    "change_secrets",
    "weaken_auth",
    "weaken_rls",
    "change_tenant_isolation",
    "approve_financial_actions",
    "approve_permission_changes",
)

_FORBIDDEN_NAME_SNIPPETS = (
    "merge_pr",
    "git_push_main",
    "railway_variable_set",
    "deploy_production",
    "alembic_upgrade",
    "disable_rls",
    "grant_permission",
)


def has_production_authority(action: str) -> bool:
    """Always false. The Improvement Loop cannot perform production actions."""
    _ = action
    return False


def allowed_outputs() -> tuple[str, ...]:
    return (
        "failure_record",
        "fix_contract",
        "isolated_candidate_description",
        "test_results",
        "recommendation",
        "improvement_run.json",
    )


def assert_no_production_authority(root: Path | None = None) -> bool:
    """Fail closed if this package grows a production-mutation hook."""
    hits = scan_package_for_mutation_hooks(root)
    if hits:
        raise AssertionError(f"improvement package defines mutation hooks: {hits}")
    leaked = [action for action in FORBIDDEN_ACTIONS if has_production_authority(action)]
    if leaked:
        raise AssertionError(f"production authority leaked for: {leaked}")
    return True


def scan_package_for_mutation_hooks(root: Path | None = None) -> list[str]:
    """Static scan: Improvement Loop modules must not define mutation hooks."""
    base = root or Path(__file__).resolve().parent
    hits: list[str] = []
    for path in sorted(base.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = ""
            if isinstance(node, ast.FunctionDef):
                name = node.name
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name = node.func.id
            lowered = name.casefold()
            for snippet in _FORBIDDEN_NAME_SNIPPETS:
                if snippet in lowered:
                    hits.append(f"{path.name}:{name}")
    return hits
